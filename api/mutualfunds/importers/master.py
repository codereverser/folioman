import csv
import datetime
import io
import logging
import re

import tablib
from dateutil.parser import parse as dateparse
from import_export.fields import Field
from import_export.resources import ModelResource
from import_export.instance_loaders import CachedInstanceLoader
from import_export.results import Error, Result, RowResult
from rapidfuzz import fuzz, process

from mutualfunds.models import AMC, FundCategory, FundScheme
from mutualfunds.importers.fetcher import (
    fetch_amfi_scheme_data,
    fetch_bse_star_master_data,
    fetch_quandl_amfi_metadata,
)

logger = logging.getLogger(__name__)


class FundSchemeResource(ModelResource):
    amc_id = Field(attribute="amc_id", column_name="amc_id")
    category_id = Field(attribute="category_id", column_name="category_id")

    class Meta:
        model = FundScheme
        import_id_fields = ["sid"]
        fields = (
            "sid",
            "name",
            "rta",
            "plan",
            "rta_code",
            "amc_code",
            "amfi_code",
            "isin",
            "start_date",
            "end_date",
            "amc_id",
            "category_id",
        )
        skip_unchanged = True
        use_bulk = True
        skip_diff = False
        instance_loader_class = CachedInstanceLoader
        batch_size = 2000


def import_master_scheme_data(master_csv_data=None):
    """Import BSE StarMF master data into Database"""

    amc_cache = {}

    if master_csv_data is None:
        master_csv_data = fetch_bse_star_master_data()
    quandl_data = fetch_quandl_amfi_metadata()
    amfi_data = fetch_amfi_scheme_data()

    end_date_cutoff = (datetime.date.today() - datetime.timedelta(weeks=1)).isoformat()

    subtypes = {f.subtype: f.id for f in FundCategory.objects.only("subtype").all()}
    categories = {str(f): f.id for f in FundCategory.objects.all()}
    category_map = {
        "EQUITY": "EQUITY - NA",
        "DEBT": "DEBT - NA",
        "HYBRID": "HYBRID - NA",
        "MIP": "DEBT - INCOME",
        "STP": "DEBT - INCOME",
        "FOF": "OTHER - FOF DOMESTIC",
        "INCOME": "DEBT - INCOME",
    }

    import_headers = [
        "sid",
        "name",
        "rta",
        "plan",
        "rta_code",
        "amc_code",
        "amfi_code",
        "isin",
        "start_date",
        "end_date",
        "amc_id",
        "category_id",
    ]
    dataset = tablib.Dataset(headers=import_headers)
    master_data = {}
    with io.StringIO(master_csv_data) as fp:
        reader = csv.DictReader(fp, delimiter="|")
        for row in reader:
            isin = row["ISIN"].strip()
            code = row["Scheme Code"].strip()
            scheme_name = row["Scheme Name"].strip()
            if isin in quandl_data:
                _, scheme_type = quandl_data[isin]
                if scheme_type in ("payout", "reinvest") and scheme_type not in scheme_name.lower():
                    continue
            if isin in master_data and len(master_data[isin]["Scheme Code"]) <= len(code):
                continue
            master_data[isin] = row

    for isin, row in master_data.items():
        scheme_name = row["Scheme Name"].strip()
        amc_code = row["AMC Code"].strip()
        if amc_code not in amc_cache:
            try:
                amc = AMC.objects.get(code=amc_code)
            except AMC.DoesNotExist:
                amc = AMC(name=amc_code, code=amc_code)
                amc.save()
            amc_cache[amc_code] = amc.pk
        amfi_code = None
        category_id = None
        scheme_end_date = None
        if isin in quandl_data:
            amfi_code = quandl_data[isin][0]["code"]
            if amfi_code in amfi_data:
                cat_str = amfi_data[amfi_code]["Scheme Category"]
                if cat_str.lower().strip() == "growth":
                    category_id = categories["EQUITY - NA"]
                else:
                    closest_match, *_ = process.extractOne(
                        cat_str, categories.keys(), scorer=fuzz.token_sort_ratio
                    )
                    category_id = categories[closest_match]
            end_date = quandl_data[isin][0].get("to_date")
            if re.search(r"\d{4}-\d{2}-\d{2}", end_date) and end_date < end_date_cutoff:
                scheme_end_date = end_date
        if category_id is None:
            category = row["Scheme Type"].strip().upper().split()[0]
            if category in category_map:
                real_category = category_map[category]
                category_id = categories[real_category]
            else:
                match, score, _ = process.extractOne(
                    row["Scheme Name"].split("-")[0],
                    subtypes.keys(),
                    scorer=fuzz.token_set_ratio,
                )
                if score >= 50:
                    category_id = subtypes[match]
                else:
                    category_id = categories["OTHER - NA"]

        dataset.append(
            [
                int(row["Unique No"]),
                scheme_name,
                row["RTA Agent Code"].strip(),
                "DIRECT" if "DIRECT" in row["Scheme Plan"] else "REGULAR",
                row["Channel Partner Code"].strip(),
                amc_code,
                amfi_code,
                isin.strip(),
                dateparse(row["Start Date"].strip()).date(),
                dateparse(scheme_end_date or row["End Date"].strip()).date(),
                amc_cache[amc_code],
                category_id,
            ]
        )
    logger.info("Starting Import")
    resource = FundSchemeResource()
    result: Result = resource.import_data(dataset, dry_run=False)
    logger.info("Import completed")
    if result.has_errors():
        logger.error("Import failed. Printing the top 10 errors")
        item: RowResult
        for item in result.rows:
            error: Error
            for error in item.errors:
                logger.error(
                    f"RowResult id: {getattr(item, 'object_id', 'N/A')} :: Error - {error}"
                )
    return result.totals
