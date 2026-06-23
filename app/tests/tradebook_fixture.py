"""Zerodha tradebook fixtures → canonical CSV (mirrors frontend ``tradebook.ts``)."""

from __future__ import annotations

import csv
import io
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "zerodha"

CANONICAL_COLUMNS = (
    "security_type",
    "name",
    "symbol",
    "isin",
    "date",
    "transaction_type",
    "units",
    "price",
    "amount",
    "fees",
    "stamp_duty",
    "brokerage",
    "currency",
    "source_ref",
    "folio_number",
    "broker",
)

# Auto-detect mapping for Zerodha Console equity tradebook exports.
ZERODHA_HEADERS = {
    "date": "trade_date",
    "transaction_type": "trade_type",
    "units": "quantity",
    "price": "price",
    "symbol": "symbol",
    "isin": "isin",
    "source_ref": "trade_id",
}

_DEFAULT_DEMAT = "1208160000000001"
_DEFAULT_BROKER = "Zerodha"


def read_zerodha_fixture(name: str) -> list[dict[str, str]]:
    """Load one sanitised Zerodha CSV from ``fixtures/zerodha/``."""
    path = FIXTURES_DIR / name
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def zerodha_to_canonical_rows(
    zerodha_rows: list[dict[str, str]],
    *,
    folio_number: str = _DEFAULT_DEMAT,
    broker: str = _DEFAULT_BROKER,
    security_type: str = "equity",
) -> list[dict[str, str]]:
    """Project Zerodha rows onto the backend canonical shape."""
    out: list[dict[str, str]] = []
    for raw in zerodha_rows:
        row = {key: "" for key in CANONICAL_COLUMNS}
        for key, header in ZERODHA_HEADERS.items():
            row[key] = (raw.get(header) or "").strip()
        if not any(
            row[k] for k in ("date", "transaction_type", "units", "price", "symbol", "isin")
        ):
            continue
        if not row["name"]:
            row["name"] = row["symbol"] or row["isin"]
        row["security_type"] = security_type
        row["folio_number"] = folio_number
        row["broker"] = broker
        out.append(row)
    return out


def canonical_csv_bytes(
    rows: list[dict[str, str]],
    *,
    columns: tuple[str, ...] = CANONICAL_COLUMNS,
) -> bytes:
    """Serialise canonical rows to UTF-8 CSV bytes."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row.get(col, "") for col in columns})
    return buffer.getvalue().encode("utf-8")


def load_canonical_fixture(
    zerodha_name: str,
    *,
    folio_number: str = _DEFAULT_DEMAT,
    broker: str = _DEFAULT_BROKER,
) -> bytes:
    """Read a Zerodha fixture and return canonical CSV bytes ready for ``process_csv``."""
    rows = zerodha_to_canonical_rows(
        read_zerodha_fixture(zerodha_name),
        folio_number=folio_number,
        broker=broker,
    )
    return canonical_csv_bytes(rows)
