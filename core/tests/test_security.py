"""Security domain model."""

import pytest
from folioman_core.models import Security, SecurityType
from pydantic import ValidationError


def test_security_mf_with_amfi_code():
    sec = Security(
        type=SecurityType.MF,
        name="Parag Parikh Flexi Cap",
        amfi_code="122639",
        currency="inr",
    )
    assert sec.type is SecurityType.MF
    assert sec.currency == "INR"
    assert sec.amfi_code == "122639"
    assert sec.isin == ""


def test_security_mf_with_isin():
    sec = Security(
        type=SecurityType.MF,
        name="Sample MF",
        isin="INF109K01VQ4",
    )
    assert sec.isin == "INF109K01VQ4"


def test_security_equity_with_symbol():
    sec = Security(
        type=SecurityType.EQUITY,
        name="Reliance Industries",
        symbol="RELIANCE",
        exchange="NSE",
    )
    assert sec.symbol == "RELIANCE"
    assert sec.exchange == "NSE"


def test_security_crypto_with_coin_id_metadata():
    sec = Security(
        type=SecurityType.CRYPTO,
        name="Bitcoin",
        metadata={"coin_id": "bitcoin"},
    )
    assert sec.metadata["coin_id"] == "bitcoin"


def test_security_fd_with_principal_metadata():
    sec = Security(
        type=SecurityType.FD,
        name="HDFC FD",
        metadata={"principal": "100000", "rate": "7.1"},
    )
    assert sec.metadata["principal"] == "100000"


def test_security_round_trip_json():
    sec = Security(
        type=SecurityType.ETF,
        name="Nippon India ETF Nifty BeES",
        isin="INF204KB14I2",
        symbol="NIFTYBEES",
        exchange="NSE",
    )
    restored = Security.model_validate_json(sec.model_dump_json())
    assert restored == sec


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (
            {"type": SecurityType.MF, "name": "No identifiers"},
            "amfi_code or isin",
        ),
        (
            {"type": SecurityType.EQUITY, "name": "No identifiers"},
            "symbol or isin",
        ),
        (
            {"type": SecurityType.CRYPTO, "name": "No identifiers"},
            "coin_id",
        ),
        (
            {"type": SecurityType.FD, "name": "No identifiers"},
            "principal",
        ),
    ],
)
def test_security_type_specific_validation(kwargs: dict, match: str):
    with pytest.raises(ValidationError, match=match):
        Security(**kwargs)


@pytest.mark.parametrize(
    "isin",
    ["INF109K01VQ", "INF109K01VQ44", "NOTANISIN!!!", "INF109K01VQ!"],
)
def test_security_invalid_isin(isin: str):
    with pytest.raises(ValidationError, match="ISIN"):
        Security(
            type=SecurityType.MF,
            name="Bad ISIN",
            isin=isin,
            amfi_code="122639",
        )


@pytest.mark.parametrize("currency", ["IN", "INRR", "123", ""])
def test_security_invalid_currency(currency: str):
    with pytest.raises(ValidationError, match="currency"):
        Security(
            type=SecurityType.MF,
            name="Bad currency",
            amfi_code="122639",
            currency=currency,
        )


def test_security_rejects_unknown_fields():
    with pytest.raises(ValidationError, match="extra"):
        Security(
            type=SecurityType.MF,
            name="Extra field",
            amfi_code="122639",
            unknown_field=True,  # type: ignore[call-arg]
        )


def test_security_is_frozen():
    sec = Security(type=SecurityType.MF, name="Immutable", amfi_code="122639")
    with pytest.raises(ValidationError):
        sec.name = "Changed"  # type: ignore[misc]


def test_security_hashable_and_groups_by_identity():
    a = Security(
        type=SecurityType.EQUITY, name="Reliance Industries", isin="INE002A01018", symbol="RELIANCE"
    )
    b = Security(
        type=SecurityType.EQUITY, name="Reliance Industries", isin="INE002A01018", symbol="RELIANCE"
    )
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1
    bucket: dict[Security, str] = {a: "value"}
    assert bucket[b] == "value"


def test_security_equality_ignores_name_drift():
    """Same identity, drifting descriptive name -> same dict slot.

    CAS, eCAS, and manual entries normalise scheme names differently. If
    ``name`` were part of equality, a buy parsed from one source and a sell
    parsed from another would split into separate FIFO buckets (silent
    InsufficientUnitsError) or miss the 112A integrity lookup (silent LTCG
    row drop). Identity is what defines "same security."
    """
    a = Security(
        type=SecurityType.MF,
        name="ABC Fund Growth",
        amfi_code="122639",
        isin="INF174V01317",
    )
    b = Security(
        type=SecurityType.MF,
        name="ABC Fund - Growth",  # trailing punctuation drift
        amfi_code="122639",
        isin="INF174V01317",
    )
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1
    bucket: dict[Security, str] = {a: "first"}
    bucket[b] = "second"
    assert len(bucket) == 1
    assert bucket[a] == "second"


def test_security_equality_ignores_metadata_drift():
    """Two Security rows with same identity but differing metadata are equal."""
    a = Security(
        type=SecurityType.MF,
        name="Some Fund",
        amfi_code="122639",
        metadata={"equity_oriented": True, "amc": "ABC"},
    )
    b = Security(
        type=SecurityType.MF,
        name="Some Fund",
        amfi_code="122639",
        metadata={"equity_oriented": True, "amc": ""},  # AMC missing from one source
    )
    assert a == b
    assert hash(a) == hash(b)


def test_security_equality_distinguishes_identity_fields():
    """Different identity fields -> not equal (and almost certainly different hash)."""
    a = Security(type=SecurityType.MF, name="X", amfi_code="111111", isin="INF174V01317")
    different_isin = Security(
        type=SecurityType.MF, name="X", amfi_code="111111", isin="INE002A01018"
    )
    different_amfi = Security(
        type=SecurityType.MF, name="X", amfi_code="222222", isin="INF174V01317"
    )
    different_currency = Security(
        type=SecurityType.MF, name="X", amfi_code="111111", isin="INF174V01317", currency="USD"
    )
    assert a != different_isin
    assert a != different_amfi
    assert a != different_currency


def test_security_equality_returns_notimplemented_for_other_types():
    """Cross-type comparison must not raise; Python falls back via NotImplemented."""
    sec = Security(type=SecurityType.MF, name="X", amfi_code="122639")
    assert (sec == "INF174V01317") is False
    assert (sec == None) is False  # noqa: E711 — testing operator, not bool
    assert (sec == 42) is False
