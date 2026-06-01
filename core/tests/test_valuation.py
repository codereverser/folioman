"""Portfolio valuation aggregator. No HTTP; providers mocked inline."""

from datetime import date
from decimal import Decimal

from folioman_core.models import (
    Holding,
    HoldingSource,
    Quote,
    Security,
    SecurityType,
)
from folioman_core.valuation import value_holdings


def _holding(sec: Security, units: str, *, observed: str | None = None) -> Holding:
    return Holding(
        security=sec,
        as_of_date=date(2025, 6, 1),
        units=units,
        value_observed=observed,
        source=HoldingSource.MANUAL,
    )


_MF = Security(type=SecurityType.MF, name="Equity Fund", amfi_code="122639")
_EQ = Security(type=SecurityType.EQUITY, name="Reliance", isin="INE002A01018", symbol="RELIANCE")
_CRYPTO = Security(type=SecurityType.CRYPTO, name="Bitcoin", metadata={"coin_id": "bitcoin"})
_FOREIGN = Security(
    type=SecurityType.FOREIGN_EQUITY, name="Apple", isin="US0378331005", symbol="AAPL"
)
_FD = Security(
    type=SecurityType.FD, name="HDFC FD", metadata={"principal": "100000", "rate": "7.1"}
)


def test_mixed_asset_book_sums_in_inr():
    holdings = [
        _holding(_MF, "100"),  # @ 75 NAV  -> 7,500
        _holding(_EQ, "10"),  # @ 2850   -> 28,500
        _holding(_CRYPTO, "0.05"),  # @ 8,500,000 INR -> 425,000
    ]

    def nav_for(sec, _):
        return Decimal("75") if sec is _MF else None

    def quote_for(sec, _):
        return (
            Quote(as_of=date(2025, 6, 1), price=Decimal("2850"), currency="INR", source="t")
            if sec is _EQ
            else None
        )

    def crypto_for(sec, _):
        return (
            Quote(as_of=date(2025, 6, 1), price=Decimal("8500000"), currency="INR", source="t")
            if sec is _CRYPTO
            else None
        )

    valuation = value_holdings(
        holdings,
        as_of=date(2025, 6, 1),
        nav_provider=nav_for,
        quote_provider=quote_for,
        crypto_provider=crypto_for,
    )

    # 100 * 75 + 10 * 2850 + 0.05 * 8_500_000 = 7,500 + 28,500 + 425,000 = 461,000
    assert valuation.total_inr == Decimal("461000.00")
    assert valuation.stale_rows == []
    assert [r.value_inr for r in valuation.rows] == [
        Decimal("7500"),
        Decimal("28500"),
        Decimal("425000.00"),
    ]


def test_missing_nav_marks_row_stale_without_killing_total():
    """One scheme has no NAV today; the rest of the portfolio must still total correctly."""
    holdings = [_holding(_MF, "100"), _holding(_EQ, "10")]
    valuation = value_holdings(
        holdings,
        as_of=date(2025, 6, 1),
        nav_provider=lambda *_: None,  # MF lookup fails
        quote_provider=lambda *_: Quote(
            as_of=date(2025, 6, 1), price=Decimal("2850"), currency="INR", source="t"
        ),
    )
    assert valuation.total_inr == Decimal("28500")
    assert len(valuation.stale_rows) == 1
    assert valuation.stale_rows[0].security is _MF
    assert "NAV unavailable" in valuation.stale_rows[0].note


def test_foreign_equity_in_usd_marked_stale_with_fx_note():
    """Non-INR quote is honest about not being convertible in v1."""
    holdings = [_holding(_FOREIGN, "5")]
    valuation = value_holdings(
        holdings,
        as_of=date(2025, 6, 1),
        quote_provider=lambda *_: Quote(
            as_of=date(2025, 6, 1), price=Decimal("180"), currency="USD", source="t"
        ),
    )
    assert valuation.total_inr == Decimal("0")
    [row] = valuation.stale_rows
    assert "USD" in row.note
    assert "FX conversion" in row.note


def test_fd_uses_value_observed_from_holding():
    holdings = [_holding(_FD, "1", observed="105000.50")]
    valuation = value_holdings(holdings, as_of=date(2025, 6, 1))
    assert valuation.total_inr == Decimal("105000.50")
    assert valuation.rows[0].stale is False


def test_fd_without_value_observed_marked_stale():
    holdings = [_holding(_FD, "1")]
    valuation = value_holdings(holdings, as_of=date(2025, 6, 1))
    assert valuation.total_inr == Decimal("0")
    assert valuation.rows[0].stale is True


def test_missing_providers_mark_relevant_rows_stale():
    """When no provider is wired for a given asset class, those rows are stale."""
    holdings = [_holding(_MF, "100"), _holding(_CRYPTO, "0.05")]
    valuation = value_holdings(holdings, as_of=date(2025, 6, 1))  # no providers
    assert valuation.total_inr == Decimal("0")
    notes = [r.note for r in valuation.stale_rows]
    assert any("no NAV provider" in n for n in notes)
    assert any("no crypto quote provider" in n for n in notes)
