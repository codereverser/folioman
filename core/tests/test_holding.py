"""Holding domain model."""

from datetime import date
from decimal import Decimal

import pytest
from folioman_core.models import Holding, HoldingSource, Security, SecurityType
from pydantic import ValidationError


def test_holding_round_trip():
    holding = Holding(
        security=Security(
            type=SecurityType.EQUITY,
            name="Reliance Industries",
            isin="INE002A01018",
            symbol="RELIANCE",
        ),
        as_of_date=date(2025, 3, 15),
        units="10.0000",
        value_observed="28500.00",
        source=HoldingSource.ECAS,
        broker="ZERODHA",
        folio_number="1208160001234567",
    )
    restored = Holding.model_validate_json(holding.model_dump_json())
    assert restored == holding
    assert isinstance(restored.units, Decimal)


def test_holding_rejects_float():
    with pytest.raises(ValidationError, match="float"):
        Holding(
            security=Security(
                type=SecurityType.EQUITY,
                name="Reliance",
                symbol="RELIANCE",
                isin="INE002A01018",
            ),
            as_of_date=date(2025, 3, 15),
            units=10.5,
            source=HoldingSource.MANUAL,
        )


def test_holding_rejects_negative_units():
    with pytest.raises(ValidationError, match="negative"):
        Holding(
            security=Security(
                type=SecurityType.EQUITY,
                name="Reliance",
                symbol="RELIANCE",
                isin="INE002A01018",
            ),
            as_of_date=date(2025, 3, 15),
            units="-1",
            source=HoldingSource.MANUAL,
        )


def _equity() -> Security:
    return Security(
        type=SecurityType.EQUITY, name="Reliance", symbol="RELIANCE", isin="INE002A01018"
    )


def test_holding_rejects_negative_value_observed():
    with pytest.raises(ValidationError, match="value_observed cannot be negative"):
        Holding(
            security=_equity(),
            as_of_date=date(2025, 3, 15),
            units="10",
            value_observed="-1",
            source=HoldingSource.MANUAL,
        )


def test_holding_rejects_negative_avg_cost():
    with pytest.raises(ValidationError, match="avg_cost_observed cannot be negative"):
        Holding(
            security=_equity(),
            as_of_date=date(2025, 3, 15),
            units="10",
            avg_cost_observed="-1",
            source=HoldingSource.MANUAL,
        )
