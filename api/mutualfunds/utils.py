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
    PortfolioValue
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
    today = timezone.now().date()

    from_date1 = date(1970, 1, 1)
    if len(scheme_dates) > 0:
        from_date1 = min(scheme_dates.values())
        if isinstance(from_date1, str):
            from_date1 = dateparse(from_date1).date()

    from_date2 = date(1970, 1, 1)
    if isinstance(start_date, str) and start_date != "auto":
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

    start_date = min(from_date1, from_date2)

    qs = FolioScheme.objects
    if portfolio_id is not None:
        qs = qs.filter(folio__portfolio_id=portfolio_id)

    from_date_min = today
    schemes = qs.values_list("id", "scheme_id").all()
    dfs = []
    logger.info("Computing daily scheme values..")
    for scheme_id, fund_scheme_id in schemes:
        scheme = FolioScheme.objects.get(pk=scheme_id)

        frm_date = scheme_dates.get(scheme_id, start_date)
        if isinstance(frm_date, str):
            frm_date = dateparse(frm_date).date()

        scheme_val: SchemeValue = SchemeValue.objects.filter(scheme_id=scheme_id,
                                                             date__lt=frm_date).order_by("-date").first()

        old_txns = Transaction.objects.filter(scheme_id=scheme_id,
                                              date__lt=frm_date) \
            .annotate(type=F("sub_type")) \
            .order_by("date")
        new_txns = Transaction.objects.filter(scheme_id=scheme_id,
                                              date__gte=frm_date) \
            .annotate(type=F("sub_type")) \
            .order_by("date")

        from_date = None
        if scheme_val is not None:
            from_date = scheme_val.date
        if new_txns.count() > 0:
            from_date = min(from_date or new_txns[0].date, new_txns[0].date)
        elif scheme_val is None or scheme_val.balance <= 1e-3:
            logger.info("Ignoring scheme :: %s", scheme)
            continue

        columns = ["invested", "avg_nav", "balance", "nav", "value"]

        dates = []
        invested = []
        average = []
        balance = []

        fifo = FIFOUnits()
        for txn in old_txns:
            fifo.add_transaction(txn)
        for txn in new_txns:
            fifo.add_transaction(txn)
            dates.append(txn.date)
            invested.append(fifo.invested)
            average.append(fifo.average)
            balance.append(fifo.balance)

        if fifo.balance > 1e-3:
            to_date = today
        elif len(dates) > 0:
            to_date = dates[-1]
        else:
            logger.info("Skipping scheme :: %s", scheme)
            continue

        scheme_transactions = pd.DataFrame(
            data={"invested": invested, "avg_nav": average, "balance": balance},
            index=dates
        )

        from_date_min = min(from_date, from_date_min)

        index = pd.date_range(from_date, to_date)
        scheme_vals = pd.DataFrame(
            data=[[np.nan] * len(columns)] * len(index), index=index, columns=columns
        )
        if to_date != today:
            SchemeValue.objects.filter(scheme_id=scheme_id, date__gt=to_date).delete()
            FolioValue.objects.filter(folio__schemes__id=scheme_id, date__gt=to_date).delete()
            PortfolioValue.objects.filter(portfolio__folios__schemes__id=scheme_id, date__gt=to_date).delete()
        if scheme_val is not None:
            scheme_vals.iloc[0] = [scheme_val.invested,
                                   scheme_val.avg_nav,
                                   scheme_val.balance,
                                   scheme_val.nav,
                                   scheme_val.value]
        scheme_vals.loc[scheme_transactions.index, ["invested", "avg_nav", "balance"]] = scheme_transactions[
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
    logger.info(f"SchemeValue :: {len(final_df)} rows")
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
