"""NSE bhavcopy parsers — UDiFF (current) and legacy full bhavdata."""

from datetime import date
from decimal import Decimal

from folioman_core.price_feeds.nse_bhavcopy import parse_bhavcopy_csv, parse_legacy_csv

_ON = date(2026, 7, 1)

# UDiFF: ISO-20022-style columns; a comma-grouped, quoted close; an index row on a
# non-equity series that must be dropped.
_UDIFF = """TradDt,BizDt,Sgmt,FinInstrmTp,ISIN,TckrSymb,SctySrs,ClsPric
2026-07-01,2026-07-01,CM,STK,INE002A01018,RELIANCE,EQ,"1,263.30"
2026-07-01,2026-07-01,CM,STK,INE009A01021,INFY,EQ,1550.00
2026-07-01,2026-07-01,CM,IDX,-,NIFTY 50,IN,25000.00
"""

# Legacy: header cells carry leading spaces; comma-delimited, no grouping.
_LEGACY = """SYMBOL, SERIES, DATE, PREV_CLOSE, CLOSE_PRICE
RELIANCE, EQ, 01-Jul-2026, 1250.00, 1263.30
SGBAUG28, GB, 01-Jul-2026, 5000.00, 5050.00
"""


def test_udiff_keys_equity_close_by_symbol():
    m = parse_bhavcopy_csv(_UDIFF, _ON)
    assert m["RELIANCE"].nav == Decimal("1263.30")  # comma stripped
    assert m["RELIANCE"].date == _ON
    assert m["INFY"].nav == Decimal("1550.00")


def test_udiff_skips_non_equity_series():
    assert "NIFTY 50" not in parse_bhavcopy_csv(_UDIFF, _ON)


def test_legacy_parses_close_and_filters_series():
    m = parse_legacy_csv(_LEGACY, _ON)
    assert m["RELIANCE"].nav == Decimal("1263.30")
    assert "SGBAUG28" not in m  # GB series is not an equity series
