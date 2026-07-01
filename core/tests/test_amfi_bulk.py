"""AMFI NAVAll.txt bulk parser."""

from datetime import date
from decimal import Decimal

from folioman_core.price_feeds.amfi_bulk import parse_navall

# Header, a blank line, a bare AMC section name, then data rows: one full row, one
# with a dash reinvest ISIN, one code-only, and one with a non-numeric NAV.
_HEADER = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;"
    "Scheme Name;Net Asset Value;Date"
)
SAMPLE = "\n".join(
    [
        _HEADER,
        "",
        "Aditya Birla Sun Life Mutual Fund",
        "",
        "120503;INF209K01157;INF209K01165;Aditya Birla Banking ETF;123.4567;01-Jul-2026",
        "119551;INF209KA12Z1;-;Some Growth Plan;45.1200;01-Jul-2026",
        "100999;;;No ISIN Fund;10.0000;01-Jul-2026",
        "100000;INFXXX01234;INFYYY01234;Dud NAV Fund;N.A.;01-Jul-2026",
    ]
)


def test_keys_by_amfi_code_and_both_isins():
    m = parse_navall(SAMPLE)
    point = m["120503"]
    assert point.nav == Decimal("123.4567")
    assert point.date == date(2026, 7, 1)
    assert m["INF209K01157"] is point
    assert m["INF209K01165"] is point


def test_dash_and_blank_isins_are_not_keys():
    m = parse_navall(SAMPLE)
    assert "-" not in m
    assert "" not in m
    assert m["INF209KA12Z1"].nav == Decimal("45.1200")
    assert "100999" in m  # code-only fund still captured


def test_non_numeric_nav_row_skipped():
    m = parse_navall(SAMPLE)
    assert "100000" not in m
    assert "INFXXX01234" not in m


def test_header_line_skipped():
    # The header has 5 ';' so it clears the field-count guard, but "Net Asset Value"
    # / "Date" fail to parse as nav/date — so it never lands as an entry.
    assert "Scheme Code" not in parse_navall(SAMPLE)
