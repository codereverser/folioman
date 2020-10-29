import csv
import datetime
import io
import logging
import time

from dateutil.parser import parse as date_parse
from django.core.cache import cache
import requests

from taskman import app
from folioman.models import FolioScheme, NAVHistory, FundScheme
from .utils import download_bse_star_master_data, import_master_scheme_data

logger = logging.getLogger(__name__)


def update_scheme_code(isins, code):
    FundScheme.objects.filter(isin__in=isins, amfi_code=None).update(amfi_code=code)


@app.task(name="AMFICodeMapper")
def populate_amfi_code_cache():
    logger.info("Updating ISIN <-> AMFI code map from AMFI website")
    url = f"https://www.amfiindia.com/spages/NAVAll.txt?t={int(time.time()*1000)}"
    response = requests.get(url)
    with io.StringIO(response.text) as fp:
        reader = csv.DictReader(fp, delimiter=";")
        for row in reader:
            isins = []
            isin1 = row.get("ISIN Div Payout/ ISIN Growth")
            isin2 = row.get("ISIN Div Reinvestment")
            code = row.get("Scheme Code")
            if code is not None:
                if isin1 is not None:
                    isins.append(isin1)
                    cache.set(isin1, code, 86400)
                if isin2 is not None:
                    isins.append(isin2)
                    cache.set(isin2, code, 86400)
                if len(isins) > 0:
                    update_scheme_code(isins, code)


def get_amfi_code_from_isin(isin):
    return cache.get(isin)


@app.task(name="NAVFetcher")
def fetch_nav():
    for sid in (
        FolioScheme.objects.order_by("scheme_id")
        .values_list("scheme_id", flat=True)
        .distinct("scheme_id")
    ):
        scheme = FundScheme.objects.only("id", "amfi_code", "isin").get(pk=sid)
        code = scheme.amfi_code
        if code is None:
            code = get_amfi_code_from_isin(scheme.isin)
            if code is None:
                logger.warning("Unable to lookup code for %s" % scheme.name)
                continue
            scheme.amfi_code = code
            scheme.save()
        if scheme.amfi_code is not None:
            nav = NAVHistory.objects.filter(scheme_id=scheme.id).order_by("-date").first()
            if nav is not None:
                from_date = nav.date
                logger.info("Fetching NAV for %s from %s", scheme.name, nav.date.isoformat())
            else:
                from_date = datetime.date(1970, 1, 1)
                logger.info("Fetching NAV for %s from beginning", scheme.name)
            mfapi_url = f"https://api.mfapi.in/mf/{scheme.amfi_code}"
            response = requests.get(mfapi_url)
            data = response.json()
            for item in reversed(data["data"]):
                date = date_parse(item["date"], dayfirst=True).date()
                if date <= from_date:
                    continue
                NAVHistory.objects.get_or_create(
                    scheme_id=scheme.id, date=date, defaults={"nav": item["nav"]}
                )


@app.task(name="UpdateMFSchemes")
def update_mf_schemes():
    master_csv = download_bse_star_master_data()
    return import_master_scheme_data(master_csv)
