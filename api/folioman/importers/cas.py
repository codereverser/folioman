import re

from casparser.types import CASParserDataType, FolioType
from celery import chord
from dateutil.parser import parse as dateparse
from django.utils import timezone
from typing import List

from folioman.models import (
    Portfolio,
    FundScheme,
    Folio,
    FolioScheme,
    SchemeValue,
    Transaction,
)
from folioman.tasks import update_portfolios, fetch_nav
from folioman.utils import get_closest_scheme


def import_cas(data: CASParserDataType, user_id):
    investor_info = data.get("investor_info", {}) or {}
    period = data["statement_period"]

    email = (investor_info.get("email") or "").strip()
    name = (investor_info.get("name") or "").strip()
    if not (email and name):
        raise ValueError("Email or Name invalid!")

    try:
        pf = Portfolio.objects.get(email=email)
    except Portfolio.DoesNotExist:
        pf = Portfolio(
            email=email, name=name, user_id=user_id, pan=(investor_info.get("pan") or "").strip()
        )
        pf.save()

    num_created = 0
    num_total = 0
    new_folios = 0

    folios: List[FolioType] = data.get("folios", []) or []
    fund_scheme_ids = []
    for folio in folios:
        for scheme in folio["schemes"]:
            amc_code = None
            rta_code = None
            if scheme["rta"].lower() in ("ftamil", "franklin") and (
                match := re.search(r"fti(\d+)", scheme["rta_code"], re.I)
            ):
                amc_code = match.group(1)
            else:
                rta_code = scheme["rta_code"]
            try:
                scheme_id = get_closest_scheme(
                    scheme["rta"], scheme["scheme"], rta_code=rta_code, amc_code=amc_code
                )
            except ValueError:
                raise ValueError("%s :: No such scheme" % scheme["scheme"])
            scheme["scheme_id"] = scheme_id
            fund_scheme_ids.append(scheme_id)
            obj = FundScheme.objects.only("amc_id").get(pk=scheme_id)
            if "amc_id" not in folio:
                folio["amc_id"] = obj.amc_id
            elif folio["amc_id"] != obj.amc_id:
                raise ValueError("Folio %s has schemes from different AMCs" % folio["folio"])
        if "amc_id" not in folio:
            continue  # No schemes in folio
        folio_number = re.sub(r"\s+", "", folio["folio"]).strip()
        try:
            folio_obj = Folio.objects.get(
                amc_id=folio["amc_id"], number__istartswith=folio_number.split("/")[0]
            )
        except Folio.DoesNotExist:
            folio_obj = Folio(
                amc_id=folio["amc_id"],
                number=folio_number,
                portfolio_id=pf.id,
                pan=folio["PAN"],
                kyc=folio["KYC"].lower() == "ok",
                pan_kyc=folio["PANKYC"].lower() == "ok",
            )
            folio_obj.save()
            new_folios += 1
        to_date = dateparse(period["to"]).date()
        for scheme in folio["schemes"]:
            scheme_obj, _ = FolioScheme.objects.get_or_create(
                scheme_id=scheme["scheme_id"],
                folio_id=folio_obj.id,
                defaults={
                    "balance": scheme["close"],
                    "balance_date": to_date,
                },
            )
            if scheme_obj.balance_date > to_date:
                scheme_obj.balance_date = to_date
                scheme_obj.balance = scheme["close"]
                scheme_obj.save()
            if len(scheme["transactions"]) > 0:
                from_date = dateparse(scheme["transactions"][0]["date"]).date()
                SchemeValue.objects.get_or_create(
                    scheme_id=scheme_obj.id,
                    date=from_date,
                    defaults={"units": scheme["open"], "invested": 0, "nav": 0, "value": 0},
                )
            for transaction in scheme["transactions"]:
                _, created = Transaction.objects.get_or_create(
                    scheme_id=scheme_obj.id,
                    date=dateparse(transaction["date"]).date(),
                    balance=str(transaction["balance"]),
                    defaults={
                        "description": transaction["description"].strip(),
                        "amount": transaction["amount"],
                        "units": transaction["units"],
                        "nav": transaction["nav"],
                        "order_type": Transaction.get_order_type(
                            transaction["description"], transaction["amount"]
                        ),
                    },
                )
                num_created += created
                num_total += 1
    # if num_created > 0:
    fetch_nav.delay(
        scheme_ids=fund_scheme_ids, update_portfolio={"from_date": "auto", "portfolio_id": pf.id}
    )
    return {
        "num_folios": new_folios,
        "transactions": {
            "total": num_total,
            "added": num_created,
        },
    }
