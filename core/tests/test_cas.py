"""CAS / eCAS statement views."""

import json
from pathlib import Path

import pytest
from folioman_core.models import (
    Depository,
    EcasStatement,
    MfCasStatement,
    TransactionType,
)


@pytest.fixture
def mf_cas_fixture(fixtures_dir: Path) -> dict:
    return json.loads((fixtures_dir / "mf_cas_statement.json").read_text())


@pytest.fixture
def ecas_fixture(fixtures_dir: Path) -> dict:
    return json.loads((fixtures_dir / "ecas_statement.json").read_text())


def test_mf_cas_statement_from_fixture(mf_cas_fixture: dict):
    statement = MfCasStatement.model_validate(mf_cas_fixture)
    assert statement.pan_masked == "XXXX1234X"
    assert len(statement.schemes) == 1
    scheme = statement.schemes[0]
    assert scheme.security.amfi_code == "122639"
    assert len(scheme.transactions) == 2
    assert scheme.transactions[0].transaction_type is TransactionType.BUY
    assert scheme.closing_units is not None


def test_ecas_statement_from_fixture(ecas_fixture: dict):
    statement = EcasStatement.model_validate(ecas_fixture)
    assert statement.depository is Depository.CDSL
    assert len(statement.accounts) == 1
    account = statement.accounts[0]
    assert account.folio.broker == "ZERODHA"
    assert len(account.holdings) == 2
    assert account.holdings[0].security.symbol == "RELIANCE"


def test_cas_fixtures_round_trip_json(mf_cas_fixture: dict, ecas_fixture: dict):
    mf = MfCasStatement.model_validate(mf_cas_fixture)
    ecas = EcasStatement.model_validate(ecas_fixture)
    assert MfCasStatement.model_validate_json(mf.model_dump_json()) == mf
    assert EcasStatement.model_validate_json(ecas.model_dump_json()) == ecas
