import datetime
import logging
import time

import requests
from casparser_isin.cli import update_isin_db, print_version
from dateutil.parser import parse as date_parse
from django.utils import timezone
from django_celery_beat.models import PeriodicTask
from requests.exceptions import RequestException, Timeout
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.utils import aware_utcnow

from taskman import app
from .importers.master import import_master_scheme_data
from .models import FolioScheme, NAVHistory, FundScheme
from .utils import update_portfolio_value

logger = logging.getLogger(__name__)


@app.task(
    bind=True,
    name="NAVFetcher",
    autoretry_for=(RequestException, Timeout),
    retry_backoff=60 * 60,
    retry_backoff_max=5 * 60 * 60,
    default_retry_delay=30 * 60,
    retry_kwargs={"max_retries": 6},
)
def fetch_nav(self, scheme_ids=None, update_portfolio_kwargs=None):
    qs = FolioScheme.objects
    if isinstance(scheme_ids, list):
        qs = qs.filter(scheme_id__in=scheme_ids)
    for sid in qs.order_by("scheme_id").values_list("scheme_id", flat=True).distinct("scheme_id"):
        scheme = FundScheme.objects.only("id", "amfi_code", "isin").get(pk=sid)
        code = scheme.amfi_code
        if code is None:
            logger.warning("Unable to lookup code for %s" % scheme.name)
            continue
        if scheme.amfi_code is not None:
            nav = NAVHistory.objects.filter(scheme_id=scheme.id).order_by("-date").first()
            if nav is not None:
                from_date = nav.date
                logger.info("Fetching NAV for %s from %s", scheme.name, nav.date.isoformat())
            else:
                from_date = datetime.date(1970, 1, 1)
                logger.info("Fetching NAV for %s from beginning", scheme.name)
            today = timezone.now().date()
            logger.info("today's date %s ", today.isoformat())
            if today.weekday() == 5:  # Saturday
                today = today - datetime.timedelta(days=1)
            elif today.weekday() == 6:  # Sunday
                today = today - datetime.timedelta(days=2)
            if from_date < today - datetime.timedelta(days=1):
                mfapi_url = f"https://api.mfapi.in/mf/{scheme.amfi_code}"
                response = requests.get(mfapi_url, timeout=60)
                data = response.json()
                for item in reversed(data["data"]):
                    date = date_parse(item["date"], dayfirst=True).date()
                    if date <= from_date:
                        continue
                    NAVHistory.objects.get_or_create(
                        scheme_id=scheme.id, date=date, defaults={"nav": item["nav"]}
                    )
                time.sleep(2)
    kwargs = {}
    if isinstance(update_portfolio_kwargs, dict):
        kwargs.update(update_portfolio_kwargs)
    else:
        task = (
            PeriodicTask.objects.filter(task=self.name)
            .only("last_run_at")
            .order_by("-last_run_at")
            .first()
        )
        if task and task.last_run_at:
            kwargs.update(from_date="auto")
    logger.info("Calling update portfolios with arguments %s", str(kwargs))
    update_portfolios.delay(**kwargs)


@app.task(
    name="UpdateMFSchemes",
    autoretry_for=(RequestException, Timeout),
    retry_backoff=60 * 60,
    retry_backoff_max=5 * 60 * 60,
    default_retry_delay=30 * 60,
    retry_kwargs={"max_retries": 6},
)
def update_mf_schemes():
    retval = import_master_scheme_data()
    return retval


@app.task(
    name="UpdatePortfolios",
)
def update_portfolios(from_date=None, portfolio_id=None, scheme_dates=None):
    update_portfolio_value(
        start_date=from_date, portfolio_id=portfolio_id, scheme_dates=scheme_dates
    )


@app.task(name="FlushExpiredTokens")
def flush_expired_tokens():
    OutstandingToken.objects.filter(expires_at__lte=aware_utcnow()).delete()


@app.task(name="UpdateCASParserISIN")
def update_casparser_isin():
    print("Updating casparser isin db")
    update_isin_db()
    print("casparser_isin: new version info")
    print_version()
