"""Local real-PDF smoke for the eCAS import.

Runs only where ~/casparser-private/{nsdl,cdsl} exists (the developer's
machine); skipped everywhere else. No real PDF / password is committed.
Asserts only structural counts — never prints investor PII.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from folioman_app.models import Holding, Investor
from folioman_app.tasks.import_ecas import persist_ecas_statement
from folioman_core.ecas_parser import read_ecas
from folioman_core.parser import CASParseError, CASPasswordError

_PRIVATE = Path.home() / "casparser-private"
_ECAS_DIRS = [_PRIVATE / "nsdl", _PRIVATE / "cdsl"]

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not any(d.exists() for d in _ECAS_DIRS),
        reason="real eCAS PDFs not present (local-only smoke)",
    ),
]


def _passwords(pdf: Path) -> list[str]:
    # Look for password.txt next to the PDF or in an ancestor under casparser-private.
    for parent in [pdf.parent, *pdf.parents]:
        pw = parent / "password.txt"
        if pw.exists():
            return [*pw.read_text().split(), ""]
        if parent == _PRIVATE:
            break
    return [""]


def _first_parseable():
    for base in _ECAS_DIRS:
        if not base.exists():
            continue
        for pdf in sorted(base.rglob("*.pdf")):
            content = pdf.read_bytes()
            for password in _passwords(pdf):
                try:
                    statement = read_ecas(io.BytesIO(content), password)
                except (CASPasswordError, CASParseError):
                    continue
                if statement.accounts:
                    return statement
    return None


def test_real_ecas_imports_end_to_end():
    statement = _first_parseable()
    if statement is None:
        pytest.skip("no parseable eCAS / password found in the local folder")

    from folioman_app.api.auth import get_local_user

    investor = Investor.objects.create(name="Smoke Investor", owned_by=get_local_user())
    summary = persist_ecas_statement(investor, statement, source_ref="smoke")

    assert summary["accounts"] >= 1
    assert summary["holdings_created"] >= 1
    assert Holding.objects.filter(investor=investor).count() == summary["holdings_created"]

    # Re-importing the same statement replaces the snapshot (removes nothing,
    # adds no duplicates).
    again = persist_ecas_statement(investor, statement, source_ref="smoke-2")
    assert again["holdings_removed"] == 0
    assert Holding.objects.filter(investor=investor).count() == summary["holdings_created"]
