import csv
import io
import logging
import re

from django.conf import settings
from lxml.html import fromstring
import requests
from requests.utils import default_user_agent

from folioman.models import AMC, FundCategory, Scheme

logger = logging.getLogger(__name__)


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
                Scheme.objects.get(name=scheme_name)
            except Scheme.DoesNotExist:
                scheme = Scheme(
                    name=scheme_name,
                    amc_id=amc_cache[amc_code],
                    rta=row["RTA Agent Code"].strip(),
                    category_id=cat_cache[category],
                    plan="DIRECT" if "DIRECT" in row["Scheme Plan"] else "NORMAL",
                    rta_code=row["Channel Partner Code"].strip(),
                    amc_code=row["AMC Scheme Code"].strip(),
                    isin=row["ISIN"].strip(),
                )
                inserted += 1
                scheme.save()
    return total, valid, inserted
