from datetime import date
from decimal import Decimal
import logging
import re
from django.db.models import F, Sum
from django.utils import timezone
from fuzzywuzzy import process

from folioman.models import (
    FundScheme,
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


def date_range(from_date: date, to_date: date):
    for ordinal in range(from_date.toordinal(), to_date.toordinal()):
        yield date.fromordinal(ordinal)


def update_portfolio_value(start_date=None, portfolio_id=None):

    qs1 = Transaction.objects
    qs2 = SchemeValue.objects
    if portfolio_id is not None:
        qs1 = qs1.filter(scheme__folio__portfolio_id=portfolio_id)
        qs2 = qs2.filter(scheme__folio__portfolio_id=portfolio_id)

    if isinstance(start_date, date):
        qs1 = qs1.filter(modified__gte=start_date)
        # qs2 = qs2.filter(date__gte=start_date)

    qs1 = qs1.order_by("date")[:1]
    qs2 = qs2.order_by("date")[:1]
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
    scheme_vals = {}

    sv_qs = SchemeValue.objects.filter(date__lt=from_date)
    if portfolio_id is not None:
        sv_qs = sv_qs.filter(scheme__folio__portfolio_id=portfolio_id)
    for sv in sv_qs.order_by("scheme_id", "-date").distinct("scheme_id").only("scheme_id", "units"):
        scheme_vals[sv.scheme_id] = {
            "units": sv.units,
            "value": sv.value,
            "txn": False,
            "invested": sv.invested,
            "nav": sv.nav,
        }
    logger.info(f"Processing data from {from_date.isoformat()} to {today.isoformat()}")
    for dt in date_range(from_date, today):
        txn_qs = Transaction.objects
        if portfolio_id is not None:
            txn_qs = txn_qs.filter(scheme__folio__portfolio_id=portfolio_id)
        for transaction in txn_qs.filter(date=dt):
            fs = transaction.scheme
            if fs.id not in scheme_vals:
                scheme_vals[fs.id] = {
                    "units": Decimal(0),
                    "value": Decimal(0),
                    "txn": False,
                    "nav": Decimal(0),
                    "invested": Decimal(0),
                }

            if transaction.amount > 0:
                scheme_vals[fs.id]["invested"] += transaction.amount
            elif transaction.amount < 0:
                if scheme_vals[fs.id]["nav"] < 1e-2:
                    logger.warning("Invalid transaction detected!")
                else:
                    scheme_vals[fs.id]["invested"] += scheme_vals[fs.id]["nav"] * transaction.units
            scheme_vals[fs.id]["units"] += transaction.units
            if scheme_vals[fs.id]["units"] >= 1e-3:
                scheme_vals[fs.id]["nav"] = (
                    scheme_vals[fs.id]["invested"] / scheme_vals[fs.id]["units"]
                )
            else:
                scheme_vals[fs.id]["nav"] = Decimal(0)

            scheme_vals[fs.id]["txn"] = True
        for fs_id, scheme_val in scheme_vals.items():
            if scheme_val["units"] < 1e-3 and not scheme_val["txn"]:
                continue  # Scheme has no units and has no transaction on current date.
            fs = FolioScheme.objects.get(pk=fs_id)
            qs = NAVHistory.objects.filter(scheme_id=fs.scheme.id, date__lte=dt).order_by("-date")[
                :1
            ]
            if qs.count() == 0:
                logger.warning(
                    "NAV not available for %s on date %s", fs.scheme.name, dt.isoformat()
                )
                scheme_val["txn"] = False
                continue
            else:
                nav = qs[0].nav
            scheme_val["value"] = scheme_val["units"] * nav
            if (scheme_val["units"] < 1e-3 or scheme_val["value"] < 1e-3) and not scheme_val["txn"]:
                # Only save if 0 units has happened due to a transaction.
                continue
            scheme_val["txn"] = False
            SchemeValue.objects.update_or_create(
                scheme_id=fs_id,
                date=dt,
                defaults={
                    "units": scheme_val["units"],
                    "value": scheme_val["value"],
                    "invested": scheme_val["invested"],
                    "nav": scheme_val["nav"],
                },
            )

        sv_qs = SchemeValue.objects
        if portfolio_id is not None:
            sv_qs = sv_qs.filter(scheme__folio__portfolio_id=portfolio_id)

        for item in (
            sv_qs.filter(date=dt)
            .values(folio_id=F("scheme__folio_id"))
            .annotate(value=Sum("value"), invested=Sum("invested"))
        ):
            FolioValue.objects.update_or_create(
                folio_id=item["folio_id"],
                date=dt,
                defaults={"value": item["value"], "invested": item["invested"]},
            )

        fv_qs = FolioValue.objects
        if portfolio_id is not None:
            fv_qs = fv_qs.filter(folio__portfolio_id=portfolio_id)
        for item in (
            fv_qs.filter(date=dt)
            .values(pf_id=F("folio__portfolio_id"))
            .annotate(value=Sum("value"), invested=Sum("invested"))
        ):
            PortfolioValue.objects.update_or_create(
                portfolio_id=item["pf_id"],
                date=dt,
                defaults={"value": item["value"], "invested": item["invested"]},
            )
        logger.info("Processed %s", dt.isoformat())
