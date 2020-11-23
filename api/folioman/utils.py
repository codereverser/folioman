from datetime import date
from decimal import Decimal
import logging
import re

from django.db.models import F, Sum
from django.utils import timezone
from fuzzywuzzy import process
import numpy as np
import pandas as pd

from tablib import Dataset

from folioman.models import (
    FundScheme,
    Transaction,
    NAVHistory,
    SchemeValue,
    FolioValue,
)
from folioman.importers.daily_value import (
    DailyValueResource,
    FolioValueResource,
    PortfolioValueResource,
    SchemeValueResource,
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


def bulk_import_daily_values(resource_cls: DailyValueResource.__class__, query):
    ds = Dataset()
    ds.dict = query
    resource = resource_cls()
    result = resource.import_data(ds, dry_run=False)
    if result.has_errors():
        logger.error("Import failed. Showing first 10 errors.")
        for row in result[:10]:
            for error in row.errors:
                logger.error(error.error)
    else:
        logger.info("Import success! :: %s", str(result.totals))


def update_portfolio_value(start_date=None, portfolio_id=None):
    transactions = []
    logger.info("Fetching transactions")
    for item in (
        Transaction.objects.only(
            "date", "amount", "units", "balance", "scheme_id", "scheme__scheme_id"
        )
        .select_related("scheme")
        .order_by("scheme_id", "date")
        .all()
    ):
        transactions.append(
            [
                item.date,
                item.amount,
                item.units,
                item.balance,
                item.scheme_id,
                item.scheme.scheme_id,
            ]
        )
    df = pd.DataFrame(
        transactions, columns=["date", "amount", "units", "balance", "scheme", "fundscheme"]
    )
    scheme_ids = df.scheme.unique()
    dfs = []
    logger.info("Computing daily scheme values..")
    for scheme_id in scheme_ids:
        df1 = df[df.scheme == scheme_id].copy()
        if len(df1) == 0:
            continue

        df1["date"] = pd.to_datetime(df1["date"])

        fund_scheme_id = df1.iloc[0].fundscheme
        from_date = df1.iloc[0].date
        today = timezone.now().date()

        scheme_val = (
            SchemeValue.objects.filter(scheme_id=scheme_id, date__lt=from_date)
            .order_by("-date")
            .only("invested", "avg_nav")
            .first()
        )
        df1["invested"] = 0
        df1["avg_nav"] = 0
        if scheme_val:
            invested = scheme_val.invested
            nav = scheme_val.avg_nav
        else:
            invested = 0
            nav = 0
        for idx, row in df1.iterrows():
            if row.amount > 0:
                invested += row.amount
                nav = invested / row.balance
            else:
                invested += nav * row.units

            df1.loc[idx, "invested"] = invested
            df1.loc[idx, "avg_nav"] = nav

        if df1.iloc[-1].balance < 1e-3:
            to_date = df1.iloc[-1].date
        else:
            to_date = today

        index = pd.date_range(from_date, to_date)
        columns = ["invested", "avg_nav", "balance", "nav", "value"]
        scheme_vals = pd.DataFrame(
            data=[[np.nan] * len(columns)] * len(index), index=index, columns=columns
        )

        dfd = df1.set_index("date")
        scheme_vals.loc[dfd.index, ["invested", "avg_nav", "balance"]] = dfd[
            ["invested", "avg_nav", "balance"]
        ]

        qs = (
            NAVHistory.objects.filter(
                scheme_id=fund_scheme_id, date__gte=from_date, date__lte=to_date
            )
            .values_list("date", "nav")
            .all()
        )
        nav_df = pd.DataFrame(data=qs, columns=["date", "nav"])
        nav_df["date"] = pd.to_datetime(nav_df["date"])
        nav_df.set_index("date", inplace=True)
        scheme_vals.loc[nav_df.index, ["nav"]] = nav_df
        scheme_vals.ffill(inplace=True)
        scheme_vals.fillna(value=0, inplace=True)
        scheme_vals["value"] = scheme_vals["nav"] * scheme_vals["balance"]
        scheme_vals["scheme__id"] = scheme_id
        scheme_vals = scheme_vals.reset_index().rename(columns={"index": "date"})
        dfs.append(scheme_vals)
    final_df = pd.concat(dfs)
    dataset = Dataset().load(final_df)
    s_resource = SchemeValueResource()
    logger.info("Importing SchemeValue data")
    result = s_resource.import_data(dataset, dry_run=False)
    if result.has_errors():
        for row in result.rows[:10]:
            for error in row.errors:
                print(error.error, error.traceback)
    else:
        logger.info("Import success! :: %s", str(result.totals))
    logger.info("SchemeValue Imported")
    logger.info("Updating FolioValue")
    query = (
        SchemeValue.objects.annotate(folio__id=F("scheme__folio_id"))
        .values("date", "folio__id")
        .annotate(value=Sum("value"), invested=Sum("invested"))
    )
    bulk_import_daily_values(FolioValueResource, query)
    logger.info("FolioValue updated")
    logger.info("Updating PortfolioValue")
    query = (
        FolioValue.objects.annotate(portfolio__id=F("folio__portfolio_id"))
        .values("date", "portfolio__id")
        .annotate(value=Sum("value"), invested=Sum("invested"))
    )
    bulk_import_daily_values(PortfolioValueResource, query)
    logger.info("PortfolioValue updated")
