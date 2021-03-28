import csv
import io
import re

from dateutil.parser import parse as dateparse
from rapidfuzz import fuzz, process

from folioman.models import AMC, FundCategory, FundScheme
from .fetcher import fetch_amfi_scheme_data, fetch_bse_star_master_data, fetch_quandl_amfi_metadata


def import_master_scheme_data(master_csv_data=None):
    """Import BSE StarMF master data into Database"""

    total = inserted = valid = 0
    amc_cache = {}

    if master_csv_data is None:
        master_csv_data = fetch_bse_star_master_data()
    quandl_data = fetch_quandl_amfi_metadata()
    amfi_data = fetch_amfi_scheme_data()

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

    with io.StringIO(master_csv_data) as fp:
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

            isin = row["ISIN"]
            amfi_code = None
            category_id = None
            if isin in quandl_data:
                amfi_code = quandl_data[isin]["code"]
                if amfi_code in amfi_data:
                    cat_str = amfi_data[amfi_code]["Scheme Category"]
                    if cat_str.lower().strip() == "growth":
                        category_id = categories["EQUITY - NA"]
                    else:
                        closest_match, *_ = process.extractOne(
                            cat_str, categories.keys(), scorer=fuzz.token_sort_ratio
                        )
                        category_id = categories[closest_match]
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

            scheme_name = row["Scheme Name"].strip()
            try:
                FundScheme.objects.get(name=scheme_name)
            except FundScheme.DoesNotExist:
                scheme = FundScheme(
                    name=scheme_name,
                    amc_id=amc_cache[amc_code],
                    rta=row["RTA Agent Code"].strip(),
                    category_id=category_id,
                    plan="DIRECT" if "DIRECT" in row["Scheme Plan"] else "REGULAR",
                    rta_code=row["Channel Partner Code"].strip(),
                    amc_code=row["AMC Scheme Code"].strip(),
                    amfi_code=amfi_code,
                    isin=row["ISIN"].strip(),
                    start_date=dateparse(row["Start Date"].strip()).date(),
                    end_date=dateparse(row["End Date"].strip()).date(),
                )
                inserted += 1
                scheme.save()
    return total, valid, inserted
