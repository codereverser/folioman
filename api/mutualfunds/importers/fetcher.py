import csv
import io
import logging
import re
import zipfile

from django.conf import settings
from lxml.html import fromstring
import requests
from requests.utils import default_user_agent

logger = logging.getLogger(__name__)


def fetch_bse_star_master_data():
    """Download BSE STARMF master data file"""

    logger.info("BSE Master data not provided. Downloading.")
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": default_user_agent("folioman-python-requests"),
        }
    )
    response = session.get(settings.BSE_STARMF_SCHEME_MASTER_URL, timeout=30)
    page = fromstring(response.content)
    form_data = {
        x.get("name"): x.get("value")
        for x in page.xpath('.//form[@id="frmOrdConfirm"]//input[@type="hidden"]')
    }
    form_data.update({"ddlTypeOption": "SCHEMEMASTERPHYSICAL", "btnText": "Export to Text"})
    response = session.post(settings.BSE_STARMF_SCHEME_MASTER_URL, data=form_data, timeout=600)
    if response.status_code != 200:
        raise ValueError("Invalid response from BSE. Cannot continue...")
    logger.info("BSE Master data downloaded.")
    return response.text


def fetch_quandl_amfi_metadata():
    logger.info("Downloading quandl AMFI data...")
    params = {"api_key": settings.QUANDL_API_KEY}
    response = requests.get(settings.QUANDL_METADATA_URL, params=params, timeout=300)
    if response.status_code != 200:
        raise requests.exceptions.RequestException("Invalid response!")
    content = io.BytesIO(response.content)
    data = {}
    dividends = ("payout", "reinvest")

    def category(x):
        name = x["name"]
        desc = x["description"].lower()
        if "payout" in name.lower() or (
            "payout" in desc and "reinvest" not in desc and "growth" not in desc
        ):
            return "payout"
        elif "reinvest" in name.lower() or (
            "payout" not in desc and "reinvest" in desc and "growth" not in desc
        ):
            return "reinvest"
        return "n/a"

    with zipfile.ZipFile(content) as zipf:
        with zipf.open("AMFI_metadata.csv") as csvf:
            reader = csv.DictReader(io.TextIOWrapper(csvf, encoding="utf-8"))
            for row in reader:
                isins = re.findall(r"\sIN[a-zA-Z0-9]{10}", row["description"])

                is_dividend = False
                if len(isins) > 2:
                    raise ValueError("Unexpected layout. Debug needed")
                elif len(isins) == 2:
                    is_dividend = True
                for idx, isin in enumerate(isins):
                    if is_dividend:
                        data[isin.strip()] = row, dividends[idx]
                    else:
                        data[isin.strip()] = row, category(row)
    return data


def fetch_amfi_scheme_data():
    logger.info("Downloading AMFI scheme data...")
    response = requests.get(settings.AMFI_SCHEME_DATA_URL, timeout=300)
    if response.status_code != 200:
        raise requests.RequestException("Invalid response!")

    data = {}
    with io.StringIO(response.text) as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            code = row["Code"]
            data[code.strip()] = row
    return data
