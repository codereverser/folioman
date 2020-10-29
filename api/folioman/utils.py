import csv
from datetime import date
from decimal import Decimal
import io
import logging
import re
from typing import List

from casparser.types import CASParserDataType, FolioType
from dateutil.parser import parse as dateparse
from django.conf import settings
from django.db.models import F, Subquery, OuterRef, Sum
from django.utils import timezone
from fuzzywuzzy import process
from lxml.html import fromstring
import requests
from requests.utils import default_user_agent

from folioman.models import (
    AMC,
    FundCategory,
    FundScheme,
    Portfolio,
    Folio,
    FolioScheme,
    Transaction,
    NAVHistory,
    SchemeValue,
    FolioValue,
    PortfolioValue,
)

logger = logging.getLogger(__name__)
RTA_MAP = {"CAMS": "CAMS", "FTAMIL": "FRANKLIN", "KFINTECH": "KARVY", "KARVY": "KARVY"}


def scheme_lookup(rta, scheme_name, rta_code=None, amc_code=None):
    if rta_code is None and amc_code is None:
        raise ValueError("Either of rta_code or amc_code should be provided.")
    if rta_code is not None:
        rta_code = re.sub(r"\s+", "", rta_code)

    include = {"rta": RTA_MAP[rta.upper()]}
    exclude = {}

    if rta_code is not None:
        include["rta_code"] = rta_code
    else:
        include["amc_code"] = amc_code

    if "reinvest" in scheme_name.lower():
        include["name__icontains"] = "reinvest"
    else:
        exclude["name__icontains"] = "reinvest"

    qs = FundScheme.objects.filter(**include).exclude(**exclude)
    if qs.count() == 0 and "rta_code" in include:
        include["rta_code"] = rta_code[:-1]
        qs = FundScheme.objects.filter(**include).exclude(**exclude)
    return qs.all()


def get_closest_scheme(rta, scheme_name, rta_code=None, amc_code=None):
    qs = scheme_lookup(rta, scheme_name, rta_code=rta_code, amc_code=amc_code)
    if qs.count() == 0:
        raise ValueError("No schemes found")
    schemes = dict(qs.values_list("name", "pk"))
    key, _ = process.extractOne(scheme_name, schemes.keys())
    scheme_id = schemes[key]
    return scheme_id


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
                    defaults={"units": scheme["open"], "nav": 0, "value": 0},
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
    return {
        "num_folios": new_folios,
        "transactions": {
            "total": num_total,
            "added": num_created,
        },
    }


def download_bse_star_master_data():
    """Download BSE STARMF master data file"""

    logger.info("BSE Master data not provided. Downloading.")
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": default_user_agent("folioman-python-requests"),
        }
    )
    response = session.get(settings.BSE_STARMF_SCHEME_MASTER_URL)
    page = fromstring(response.content)
    form_data = {
        x.get("name"): x.get("value")
        for x in page.xpath('.//form[@id="frmOrdConfirm"]//input[@type="hidden"]')
    }
    form_data.update({"ddlTypeOption": "SCHEMEMASTERPHYSICAL", "btnText": "Export to Text"})
    response = session.post(settings.BSE_STARMF_SCHEME_MASTER_URL, data=form_data)
    if response.status_code != 200:
        raise ValueError("Invalid response from BSE. Cannot continue...")
    logger.info("BSE Master data downloaded.")
    return response.text


def import_master_scheme_data(master_csv: str):
    """Import BSE StarMF master data into Database"""

    total = inserted = valid = 0
    amc_cache = {}
    cat_cache = {}

    with io.StringIO(master_csv) as fp:
        reader = csv.DictReader(fp, delimiter="|")
        for row in reader:
            total += 1
            if re.search(r"-(?:I|L1)$", row["Scheme Code"]):
                continue
            valid += 1
            amc_code = row["AMC Code"].strip()
            if amc_code not in amc_cache:
                try:
                    amc = AMC.objects.get(code=amc_code)
                except AMC.DoesNotExist:
                    amc = AMC(name=amc_code, code=amc_code)
                    amc.save()
                amc_cache[amc_code] = amc.pk

            category = row["Scheme Type"].strip()
            if category not in cat_cache:
                try:
                    cat = FundCategory.objects.get(name=category)
                except FundCategory.DoesNotExist:
                    cat = FundCategory(name=category)
                    cat.save()
                cat_cache[category] = cat.pk

            scheme_name = row["Scheme Name"].strip()
            try:
                FundScheme.objects.get(name=scheme_name)
            except FundScheme.DoesNotExist:
                scheme = FundScheme(
                    name=scheme_name,
                    amc_id=amc_cache[amc_code],
                    rta=row["RTA Agent Code"].strip(),
                    category_id=cat_cache[category],
                    plan="DIRECT" if "DIRECT" in row["Scheme Plan"] else "NORMAL",
                    rta_code=row["Channel Partner Code"].strip(),
                    amc_code=row["AMC Scheme Code"].strip(),
                    isin=row["ISIN"].strip(),
                    start_date=dateparse(row["Start Date"].strip()).date(),
                    end_date=dateparse(row["End Date"].strip()).date(),
                )
                inserted += 1
                scheme.save()
    return total, valid, inserted


def date_range(from_date: date, to_date: date):
    for ordinal in range(from_date.toordinal(), to_date.toordinal()):
        yield date.fromordinal(ordinal)


def update_portfolio_value():
    pfs = Portfolio.objects.all()
    qs1 = Transaction.objects.only("date").order_by("date")[:1]
    qs2 = SchemeValue.objects.only("date").order_by("date")[:1]
    if qs1.count() == 0:
        date1 = timezone.now().date()
    else:
        date1 = qs1[0].date
    if qs2.count() == 0:
        date2 = timezone.now().date()
    else:
        date2 = qs2[0].date

    from_date = min(date1, date2)
    today = timezone.now().date()
    svals = {}

    sq = SchemeValue.objects.filter(scheme_id=OuterRef("scheme_id")).order_by("date")
    for sv in (
        SchemeValue.objects.annotate(first_date=Subquery(sq.values("date")[:1]))
        .filter(date=F("first_date"))
        .only("scheme_id", "units")
    ):
        svals[sv.scheme_id] = {"units": sv.units, "value": Decimal(0)}
    for dt in date_range(from_date, today):
        for transaction in Transaction.objects.filter(date=dt):
            fs = transaction.scheme
            if fs.id not in svals:
                svals[fs.id] = {"units": Decimal(0), "value": Decimal(0)}
            svals[fs.id]["units"] += transaction.units
        for fs_id, sval_obj in svals.items():
            fs = FolioScheme.objects.get(pk=fs_id)
            qs = NAVHistory.objects.filter(scheme_id=fs.scheme.id, date__lte=dt).order_by("-date")[
                :1
            ]
            if qs.count() == 0:
                nav = Decimal(0)
            else:
                nav = qs[0].nav
            sval_obj["value"] = sval_obj["units"] * nav
            if sval_obj["units"] < 1e-3 or sval_obj["value"] < 1e-3:
                continue
            SchemeValue.objects.update_or_create(
                scheme_id=fs_id,
                date=dt,
                defaults={
                    "units": sval_obj["units"],
                    "value": sval_obj["value"],
                    "nav": sval_obj["value"] / sval_obj["units"],
                },
            )

        for item in (
            SchemeValue.objects.filter(date=dt)
            .values(folio_id=F("scheme__folio_id"))
            .annotate(value=Sum("value"))
        ):
            FolioValue.objects.update_or_create(
                folio_id=item["folio_id"], date=dt, defaults={"value": item["value"]}
            )
        for item in (
            FolioValue.objects.filter(date=dt)
            .values(pf_id=F("folio__portfolio_id"))
            .annotate(value=Sum("value"))
        ):
            PortfolioValue.objects.update_or_create(
                portfolio_id=item["pf_id"], date=dt, defaults={"value": item["value"]}
            )
