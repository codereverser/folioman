import re
from typing import List

from casparser.types import CASParserDataType, FolioType
from dateutil.parser import parse as dateparse

from mutualfunds.models import (
    Portfolio,
    FundScheme,
    Folio,
    FolioScheme,
    SchemeValue,
    Transaction,
)
from mutualfunds.tasks import fetch_nav
from mutualfunds.utils import get_closest_scheme


def import_cas(data: CASParserDataType, user_id):
    investor_info = data.get("investor_info", {}) or {}
    period = data["statement_period"]

    email = (investor_info.get("email") or "").strip()
    name = (investor_info.get("name") or "").strip()
    if not (email and name):
        raise ValueError("Email or Name invalid!")

    folios: List[FolioType] = data.get("folios", []) or []
    try:
        pf = Portfolio.objects.get(email=email)
    except Portfolio.DoesNotExist:
        pf = Portfolio(
            email=email, name=name, user_id=user_id, pan=(folios[0].get("PAN") or "").strip()
        )
        pf.save()

    num_created = 0
    num_total = 0
    new_folios = 0

    fund_scheme_ids = []
    scheme_dates = {}
    for folio in folios:
        for scheme in folio["schemes"]:

            scheme_id = None
            if "isin" in scheme:
                qs = FundScheme.objects.filter(isin=scheme["isin"]).all()
                if qs.count() == 1:
                    fund_scheme: FundScheme = qs[0]
                    amfi_code = scheme["amfi"]
                    if (
                        fund_scheme.amfi_code is None
                        and isinstance(amfi_code, str)
                        and len(amfi_code) > 2
                    ):
                        fund_scheme.amfi_code = amfi_code
                        fund_scheme.save()
                    scheme_id = fund_scheme.id
            if scheme_id is None:
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
            if not folio["PANKYC"]:
                folio["PANKYC"] = "notok"
            if not folio["PAN"]:
                folio["PAN"] = "noregister"
            if not folio["KYC"]:
                folio["KYC"] = "notok"
            folio_obj = Folio(
                amc_id=folio["amc_id"],
                number=folio_number,
                portfolio_id=pf.id,
                pan=folio["PAN"],
                kyc=folio["KYC"].lower() == "ok",
                pan_kyc=folio["PANKYC"].lower() == "ok"
            )
            folio_obj.save()
            new_folios += 1
        to_date = dateparse(period["to"]).date()

        for scheme in folio["schemes"]:
            scheme_obj, _ = FolioScheme.objects.get_or_create(  # FIXME: update_or_create
                scheme_id=scheme["scheme_id"],
                folio_id=folio_obj.id,
            )
            if len(scheme["transactions"]) > 0:
                from_date = dateparse(scheme["transactions"][0]["date"]).date()
                SchemeValue.objects.get_or_create(
                    scheme_id=scheme_obj.id,
                    date=from_date,
                    defaults={"balance": scheme["open"], "invested": 0, "nav": 0, "value": 0},
                )
            balance = 0
            min_date = None
            for transaction in scheme["transactions"]:
                if transaction["balance"] is None:
                    transaction["balance"] = balance
                else:
                    balance = transaction["balance"]
                txn_date = dateparse(transaction["date"]).date()
                _, created = Transaction.objects.get_or_create(
                    scheme_id=scheme_obj.id,
                    date=txn_date,
                    balance=str(transaction["balance"]),
                    units=str(transaction["units"] or 0),
                    defaults={
                        "description": transaction["description"].strip(),
                        "amount": transaction["amount"] or 0.00001,
                        "nav": transaction["nav"] or 0,
                        "order_type": Transaction.get_order_type(
                            transaction["description"], transaction["amount"]
                        ),
                        "sub_type": transaction["type"],
                    },
                )
                if created:
                    min_date = min(txn_date, min_date or txn_date)
                num_created += created
                num_total += 1
            if min_date is not None:
                scheme_dates[scheme_obj.id] = min_date
    # if num_created > 0:
    fetch_nav.delay(
        scheme_ids=fund_scheme_ids,
        update_portfolio_kwargs={
            "from_date": "auto",
            "portfolio_id": pf.id,
            "scheme_dates": scheme_dates,
        },
    )
    return {
        "num_folios": new_folios,
        "transactions": {
            "total": num_total,
            "added": num_created,
        },
    }
