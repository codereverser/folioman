import csv
import io
import logging
import re

import djclick as click
from lxml.html import fromstring
import requests
from requests.utils import default_user_agent

from folioman.models import AMC, FundCategory, Scheme

BSE_STARMF_SCHEME_MASTER_URL = "https://bsestarmf.in/RptSchemeMaster.aspx"


@click.command()
@click.option(
    "-i",
    "--input_file",
    type=click.Path(exists=True, dir_okay=False),
    help="BSE StarMF Scheme Master Data (optional)",
)
def load_schemes(input_file):
    """Load BSE StarMF master data file into database"""
    amc_cache = {}
    cat_cache = {}

    logger = logging.getLogger(__name__)

    if input_file is not None:
        with open(input_file, "r") as fp:
            master_data = fp.read()
    else:
        logger.info("BSE Master data not provided. Downloading.")
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": default_user_agent("folioman-python-requests"),
            }
        )
        response = session.get(BSE_STARMF_SCHEME_MASTER_URL)
        page = fromstring(response.content)
        form_data = {
            x.get("name"): x.get("value")
            for x in page.xpath('.//form[@id="frmOrdConfirm"]//input[@type="hidden"]')
        }
        form_data.update({"ddlTypeOption": "SCHEMEMASTERPHYSICAL", "btnText": "Export to Text"})
        response = session.post(BSE_STARMF_SCHEME_MASTER_URL, data=form_data)
        if response.status_code != 200:
            raise ValueError("Invalid response from BSE. Cannot continue...")
        master_data = response.text
        logger.info("BSE Master data downloaded.")

    total = 0
    valid = 0
    inserted = 0
    logger.info("Importing to database")
    with io.StringIO(master_data) as fp:
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
            except Scheme.MultipleObjectsReturned:
                print(row)
                raise
    logger.info("Summary: Total %d :: Valid %d :: Inserted %d", total, valid, inserted)
