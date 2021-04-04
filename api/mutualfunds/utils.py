from collections import deque
from decimal import Decimal
from datetime import date, timedelta
import logging
import re
from typing import Optional, Union

from dateutil.parser import parse as dateparse
from django.db.models import F, Sum
from django.utils import timezone
from rapidfuzz import process
import numpy as np
import pandas as pd

from tablib import Dataset

from .models import (
    FolioScheme,
    FundScheme,
    Transaction,
    NAVHistory,
    SchemeValue,
    FolioValue,
)
from .importers.daily_value import (
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
    key, *_ = process.extractOne(scheme_name, schemes.keys())
    scheme_id = schemes[key]
    return scheme_id


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


class TransactionLike:
    amount: Union[Decimal, float, None]
    nav: Union[Decimal, float, None]
    units: Union[Decimal, float, None]
    type: str


class FIFOUnits:
    def __init__(
        self, balance=Decimal("0.000"), invested=Decimal("0.00"), average=Decimal("0.0000")
    ):
        self.transactions = deque()
        self.balance = balance
        self.invested = invested
        self.average = average
        self.pnl = Decimal("0.00")

    def __str__(self):
        return f"""
Number of transactions : {len(self.transactions)}
Balance                : {self.balance}
Invested               : {self.invested}
Average NAV            : {self.average}
PNL                    : {self.pnl}"""

    def add_transaction(self, txn: TransactionLike):
        """Add transaction to the FIFO Queue.
        Note: The Transactions should be sorted date-wise (preferably using the
        `sort_transactions=True` option via casparser
        """
        quantity = Decimal(str(txn.units or "0.000"))
        nav = Decimal(str(txn.nav or "0.0000"))
        if txn.amount is None:
            return
        elif txn.amount > 0 and txn.type != "STT_TAX":
            self.buy(quantity, nav, amount=txn.amount)
        elif txn.amount < 0:
            self.sell(quantity, nav)

    def sell(self, quantity: Decimal, nav: Decimal):
        original_quantity = abs(quantity)
        pending_units = original_quantity
        cost_price = Decimal("0.000")
        price = None
        while pending_units > 0:
            try:
                units, price = self.transactions.popleft()
                if units <= pending_units:
                    cost_price += units * price
                else:
                    cost_price += pending_units * price
                pending_units -= units
            except IndexError:
                break
        if pending_units < 0 and price is not None:
            # Re-add the remaining units to the FIFO queue
            self.transactions.appendleft((-1 * pending_units, price))
        self.invested -= Decimal(round(cost_price, 2))
        self.balance -= original_quantity
        self.pnl += Decimal(round(original_quantity * nav - cost_price, 2))
        if abs(self.balance) > 0.01:
            self.average = Decimal(round(self.invested / self.balance, 4))

    def buy(self, quantity: Decimal, nav: Decimal, amount: Optional[Decimal] = None):
        self.balance += quantity
        if amount is not None:
            self.invested += Decimal(amount)
        if abs(self.balance) > 0.01:
            self.average = Decimal(round(self.invested / self.balance, 4))
        self.transactions.append((quantity, nav))


def update_portfolio_value(start_date=None, portfolio_id=None, scheme_dates=None):
    if not isinstance(scheme_dates, dict):
        scheme_dates = {}
    transactions = []
    today = timezone.now().date()
    logger.info("Fetching transactions")
    qs = Transaction.objects.only(
        "date", "amount", "units", "nav", "balance", "sub_type", "scheme_id", "scheme__scheme_id"
    )

    from_date1 = date(1970, 1, 1)
    if len(scheme_dates) > 0:
        from_date1 = min(scheme_dates.values())
        if isinstance(from_date1, str):
            from_date1 = dateparse(from_date1).date()

    from_date2 = date(1970, 1, 1)
    if isinstance(start_date, str):
        from_date2 = dateparse(start_date).date()
    elif isinstance(start_date, date):
        from_date2 = start_date
    else:
        query = SchemeValue.objects
        if portfolio_id is not None:
            query = query.filter(scheme__folio__portfolio_id=portfolio_id)
        obj = query.only("date").order_by("-date").first()
        if obj is not None:
            from_date2 = obj.date

    from_date = min(from_date1, from_date2)

    if start_date is not None:
        qs = qs.filter(date__gte=from_date)

    if portfolio_id is not None:
        qs = qs.filter(scheme__folio__portfolio_id=portfolio_id)

    for item in qs.order_by("scheme_id", "date").all():
        transactions.append(
            [
                item.date,
                item.amount,
                item.units,
                item.nav,
                item.balance,
                item.sub_type,
                item.scheme_id,
            ]
        )
    df = pd.DataFrame(
        transactions, columns=["date", "amount", "units", "nav", "balance", "type", "scheme"]
    )

    qs = FolioScheme.objects
    if portfolio_id is not None:
        qs = qs.filter(folio__portfolio_id=portfolio_id)

    from_date_min = today
    schemes = qs.values_list("id", "scheme_id").all()
    dfs = []
    logger.info("Computing daily scheme values..")
    for scheme_id, fund_scheme_id in schemes:
        scheme = FolioScheme.objects.get(pk=scheme_id)

        scheme_transactions = df[df.scheme == scheme_id].copy()
        frm_date = scheme_dates.get(scheme_id, start_date)
        if isinstance(frm_date, str):
            frm_date = dateparse(frm_date).date()
        scheme_val: SchemeValue = (
            SchemeValue.objects.filter(scheme_id=scheme_id, date__lt=frm_date)
            .order_by("-date")
            .first()
        )

        from_date = None
        to_date = None
        columns = ["invested", "avg_nav", "balance", "nav", "value"]

        initial_data = None
        if scheme_val is not None:
            from_date = scheme_val.date
            if scheme_val.balance <= 1e-3:
                to_date = scheme_val.date
            else:
                to_date = today
            initial_data = {
                "invested": scheme_val.invested,
                "avg_nav": scheme_val.avg_nav,
                "balance": scheme_val.balance,
                "nav": scheme_val.nav,
                "value": scheme_val.value,
            }

        if len(scheme_transactions) == 0:
            if scheme_val is None or scheme_val.balance <= 1e-3:
                logger.info("Ignoring scheme :: %s", scheme)
                continue
        else:
            scheme_transactions["date"] = pd.to_datetime(scheme_transactions["date"])
            if from_date is None:
                from_date = scheme_transactions.iloc[0].date
            scheme_transactions["invested"] = 0
            scheme_transactions["avg_nav"] = 0
            if scheme_val:
                invested = scheme_val.invested
                balance = scheme_val.balance
                nav = Decimal(round(float(scheme_val.invested / scheme_val.balance), 4))
            else:
                invested = Decimal("0.00")
                nav = Decimal("0.0000")
                balance = Decimal("0.000")
            fifo = FIFOUnits(invested=invested, average=nav, balance=balance)
            for idx, row in scheme_transactions.iterrows():
                fifo.add_transaction(row)
                scheme_transactions.loc[idx, "invested"] = fifo.invested
                scheme_transactions.loc[idx, "avg_nav"] = fifo.average

            if scheme_transactions.iloc[-1].balance < 1e-3:
                to_date = scheme_transactions.iloc[-1].date
            else:
                to_date = today

        if from_date is None or to_date is None:
            logger.info("Ignoring scheme... :: %s", scheme)
            continue

        from_date_min = min(from_date, from_date_min)

        index = pd.date_range(from_date, to_date)
        scheme_vals = pd.DataFrame(
            data=[[np.nan] * len(columns)] * len(index), index=index, columns=columns
        )
        if initial_data is not None:
            scheme_vals.iloc[0] = initial_data.values()

        if len(scheme_transactions) > 0:
            dfd = scheme_transactions.set_index("date")
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
    if len(dfs) == 0:
        logger.info("No data found. Exiting..")
        return
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
        SchemeValue.objects.filter(date__gte=from_date_min)
        .annotate(folio__id=F("scheme__folio_id"))
        .values("date", "folio__id")
        .annotate(value=Sum("value"), invested=Sum("invested"))
    )
    bulk_import_daily_values(FolioValueResource, query)
    logger.info("FolioValue updated")
    logger.info("Updating PortfolioValue")
    query = (
        FolioValue.objects.filter(date__gte=from_date_min)
        .annotate(portfolio__id=F("folio__portfolio_id"))
        .values("date", "portfolio__id")
        .annotate(value=Sum("value"), invested=Sum("invested"))
    )
    bulk_import_daily_values(PortfolioValueResource, query)
    logger.info("PortfolioValue updated")
