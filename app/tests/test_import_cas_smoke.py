"""Local real-PDF smoke for the MF CAS import.

Runs only on a machine that has ~/casparser-private/{cams,kfin} present (the
developer's). Skipped everywhere else — no real PDF or password is committed.
Validates the full chain bytes -> casparser -> ORM on genuine statements.

Deliberately asserts only structural counts and never prints investor PII.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from folioman_app.models import Investor, Transaction
from folioman_app.tasks.import_cas import persist_mf_statement
from folioman_core.parser import CASParseError, CASPasswordError, read_mf_cas

_PRIVATE = Path.home() / "casparser-private"
_MF_DIRS = [_PRIVATE / "cams", _PRIVATE / "kfin"]

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not any(d.exists() for d in _MF_DIRS),
        reason="real CAS PDFs not present (local-only smoke)",
    ),
]


def _password_candidates(pdf_dir: Path) -> list[str]:
    pw_file = pdf_dir / "password.txt"
    if not pw_file.exists():
        return [""]
    # Unknown format — try every whitespace-separated token, plus empty.
    tokens = pw_file.read_text().split()
    return [*tokens, ""]


def _first_parseable(pdf_dir: Path):
    """Return (content_bytes, working_password) for the first parseable PDF, or None."""
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        content = pdf.read_bytes()
        for password in _password_candidates(pdf_dir):
            try:
                statement = read_mf_cas(io.BytesIO(content), password)
            except (CASPasswordError, CASParseError):
                continue
            if statement.schemes:
                return content, password
    return None


def test_real_mf_cas_imports_end_to_end():
    pdf_dir = next(d for d in _MF_DIRS if d.exists())
    found = _first_parseable(pdf_dir)
    if found is None:
        pytest.skip("no parseable MF CAS / password found in the local folder")
    content, password = found

    # Same path production uses: BytesIO -> casparser -> ORM persist + reconcile.
    from folioman_app.api.auth import get_local_user

    statement = read_mf_cas(io.BytesIO(content), password)
    investor = Investor.objects.create(name="Smoke Investor", owned_by=get_local_user())
    summary = persist_mf_statement(investor, statement, source_ref="smoke")

    # Structural assertions only — never print names/PANs/holdings.
    assert summary["schemes"] >= 1
    assert summary["transactions_created"] >= 1
    assert Transaction.objects.filter(investor=investor).count() == summary["transactions_created"]

    # Re-import is idempotent.
    again = persist_mf_statement(investor, statement, source_ref="smoke-2")
    assert again["transactions_created"] == 0
