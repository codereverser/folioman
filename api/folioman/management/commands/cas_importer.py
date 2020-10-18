import re
import logging

import casparser
from dateutil import parser as date_parser
import djclick as click

from folioman.models import AMC, Portfolio, Folio, Scheme, FolioScheme, Transaction

RTA_MAP = {"CAMS": "CAMS", "FTAMIL": "FRANKLIN", "KFINTECH": "KARVY", "KARVY": "KARVY"}


def scheme_lookup(rta, rta_code, scheme_name):
    rta_code = re.sub(r"\s+", "", rta_code)
    search_map = {"rta": RTA_MAP[rta], "rta_code": rta_code}
    if "reinvest" in scheme_name.lower():
        search_map.update({"name__icontains": "reinvest"})
    try:
        return Scheme.objects.get(**search_map)
    except (Scheme.DoesNotExist, Scheme.MultipleObjectsReturned):
        raise


@click.command()
@click.option(
    "-p",
    "password",
    metavar="PASSWORD",
    prompt="Enter PDF password",
    hide_input=True,
    confirmation_prompt=False,
    help="CAS password",
)
@click.argument("input_file", type=click.Path(exists=True, dir_okay=False), metavar="CAS_PDF_FILE")
def cas_importer(password, input_file):
    logger = logging.getLogger(__name__)

    logger.info("Reading CAS PDF")
    pdf_data = casparser.read_cas_pdf(input_file, password)
    period = pdf_data["statement_period"]
    email = pdf_data["investor_info"]["email"]
    click.echo("CAS file type " + click.style(pdf_data["file_type"], fg="green", bold=True))
    click.echo(
        "CAS statement period: "
        + click.style(period["from"], fg="green", bold=True)
        + " to "
        + click.style(period["to"], fg="green", bold=True)
    )
    click.echo("Email : " + click.style(email, fg="green", bold=True))
    try:
        portfolio = Portfolio.objects.get(email=email)
    except Portfolio.DoesNotExist:
        if click.confirm("No portfolio found with the email. Add one?"):
            name = click.prompt("Enter portfolio name")
            pan = click.prompt("Enter PAN (optional)", default="")
            portfolio = Portfolio.objects.create(user_id=1, name=name, pan=pan, email=email)
            portfolio.save()
        else:
            return 1

    # Validate all folio

    for folio in pdf_data["folios"].values():
        for scheme in folio["schemes"]:
            t = scheme.copy()
            t.pop("transactions")
            print(t)
            if scheme["rta"].lower() in ("ftamil", "franklin") and re.search(
                r"fti(\d+)", scheme["rta_code"], re.I
            ):
                ft_code = re.search(r"fti(\d+)", scheme["rta_code"], re.I).group(1)
                qs = Scheme.objects.filter(rta="FRANKLIN", amc_code=ft_code)
                if "reinvestment" in scheme["scheme"].lower():
                    qs = qs.filter(name__icontains="reinvestment")
                if qs.count() != 1:
                    print(qs.count())
                    raise ValueError("Cannot resolve scheme - %s" % scheme["scheme"])
                obj = qs[0]
            else:
                try:
                    obj = scheme_lookup(scheme["rta"], scheme["rta_code"], scheme["scheme"])
                except Scheme.DoesNotExist:
                    try:
                        obj = scheme_lookup(
                            scheme["rta"], scheme["rta_code"][:-1], scheme["scheme"]
                        )
                    except Scheme.DoesNotExist:
                        raise ValueError("Scheme not found! - %s" % scheme["scheme"])
                    except Scheme.MultipleObjectsReturned:
                        raise ValueError("Multiple Schemes found! - %s" % scheme["scheme"])
                except Scheme.MultipleObjectsReturned:
                    raise ValueError("Multiple Schemes found! - %s" % scheme["scheme"])
            scheme["scheme_id"] = obj.id
            if "amc_id" not in folio:
                folio["amc_id"] = obj.amc_id
            elif folio["amc_id"] != obj.amc_id:
                raise ValueError(
                    "Folio %s validation error :: has %s, got %s",
                    folio["folio"],
                    AMC.objects.get(pk=folio["amc_id"]).name,
                    obj.amc.name,
                )
        folio_obj, _ = Folio.objects.update_or_create(
            amc_id=folio["amc_id"],
            number=folio["folio"],
            defaults={
                "portfolio_id": portfolio.id,
                "pan": folio["PAN"],
                "kyc": folio["KYC"].lower() == "ok",
                "pan_kyc": folio["PANKYC"].lower() == "ok",
            },
        )
        for scheme in folio["schemes"]:
            fs_obj, _ = FolioScheme.objects.get_or_create(
                scheme_id=scheme["scheme_id"],
                folio_id=folio_obj.id,
                defaults={
                    "balance": scheme["close"],
                    "balance_date": date_parser.parse(period["to"]).date(),
                },
            )
            if fs_obj.balance_date > date_parser.parse(period["to"]).date():
                fs_obj.balance_date = date_parser.parse(period["to"])
                fs_obj.balance = scheme["close"]
                fs_obj.save()

            for transaction in scheme["transactions"]:
                qs = Transaction.objects.filter(
                    scheme_id=fs_obj.id, date=transaction["date"], amount=transaction["amount"]
                )
                if qs.count() == 0:
                    t = Transaction(
                        scheme_id=fs_obj.id,
                        date=transaction["date"],
                        amount=transaction["amount"],
                        units=transaction["units"],
                        nav=transaction["nav"],
                        order_type=Transaction.OrderType.REDEEM
                        if transaction["amount"] < 0
                        else Transaction.OrderType.BUY,
                    )
                    t.save()
