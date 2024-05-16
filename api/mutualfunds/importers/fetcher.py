import csv
import io
import logging
import re

import requests
from django.conf import settings
from lxml.html import fromstring
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


def fetch_amfi_code_isin_mapping():
    logger.info("Downloading NAVAll from AMFI")
    response = requests.get(settings.AMFI_NAVALL_URL, timeout=300)
    if response.status_code != 200:
        raise requests.RequestException("Invalid response!")
    scheme_code_re = re.compile('\d{6}')
    data = {}
    for row in response.content.splitlines():
        row = row.decode('utf-8')
        if re.match(scheme_code_re, row):
            if row.split(';')[1] != '-':
                data[row.split(';')[1].strip()] = row.split(';')[0].strip()
            if row.split(';')[2] != '-':
                data[row.split(';')[2].strip()] = row.split(';')[0].strip()
    return data
