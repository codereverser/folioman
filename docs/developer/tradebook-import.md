# Developer note — broker tradebook import

How equity tradebooks flow from the Vue wizard to `process_csv`, and where to change
each layer.

## Architecture

```
Broker CSV/XLSX
    → parseTabularFile (SheetJS, client-side)
    → autoDetectMapping + buildCanonicalRows (tradebook.ts)
    → toCsv (csv.ts)
    → POST /api/investors/{id}/imports/csv
    → process_csv (import_csv.py)
    → reconcile_after_import
```

The backend **never** parses Zerodha-specific columns. The frontend owns column
mapping and emits the **canonical CSV** contract.

## Canonical CSV contract

Defined in `app/src/folioman_app/tasks/import_csv.py` and mirrored in
`frontend/src/utils/tradebook.ts` (`CANONICAL_COLUMNS`).

**Required columns:** `security_type`, `name`, `date`, `transaction_type`, `units`, `price`

**Optional:** `symbol`, `isin`, `amfi_code`, `coin_id`, `principal`, `amount`, `fees`,
`stamp_duty`, `brokerage`, `currency`, `source_ref`, `folio_number`, `broker`

Per-import constants injected by the wizard (not mapped from file columns):

- `security_type` — always `equity` for the stock tradebook flow
- `folio_number` — demat BO ID chosen by the user
- `broker` — broker name for the folio

`name` is required by the backend schema but may be provisional (`symbol` or `isin`
fallback in `buildCanonicalRows`). After import, `resolve_equity_identity` in
`services/equity_identity.py` overwrites name/symbol/exchange from `casparser-isin`.

### Dedup and idempotency

`process_csv._dedup_key` hashes security identity, date, type, units, price, charges,
currency, and **`source_ref`** (broker trade id). Two fills that look identical but
carry different `trade_id` values stay distinct; re-importing the same file yields
zero new rows.

Content-hash dedup at the job level also prevents duplicate file uploads.

### Charge semantics

- `brokerage` — buy-side, enters FIFO cost basis
- `fees` — sell-side STT, does not enter cost basis
- `stamp_duty` — transfer expense, does not enter cost basis

## Frontend mapping layer

| File | Role |
|---|---|
| `frontend/src/utils/parseTabular.ts` | CSV/XLSX → `{ headers, rows }` |
| `frontend/src/utils/tradebook.ts` | `CANONICAL_FIELDS`, `autoDetectMapping`, `buildCanonicalRows`, demat validation |
| `frontend/src/utils/csv.ts` | RFC-4180 CSV builder for upload |
| `frontend/src/views/TradebookImportView.vue` | Wizard UI, import API call, post-import integrity summary |

Alias lists in `CANONICAL_FIELDS` drive auto-detect (e.g. Zerodha `trade_date` → `date`).
Extend aliases when adding broker profiles; no backend change needed.

Unit tests:

- `frontend/src/utils/tradebook.test.ts` — mapping logic
- `frontend/src/utils/tradebook-fixture.test.ts` — sanitised Zerodha CSV/XLSX fixtures
- `frontend/src/utils/parseTabular.test.ts` — matrix shaping

## Backend import path

| File | Role |
|---|---|
| `app/src/folioman_app/api/imports.py` | `POST …/imports/csv` multipart upload |
| `app/src/folioman_app/tasks/import_csv.py` | Row validation, dedup, orphan/partial detection, reconcile hook |
| `app/src/folioman_app/services/equity_identity.py` | Post-import ISIN → name/symbol/exchange |
| `app/src/folioman_app/tasks/reconcile.py` | eCAS anchor + corporate-action detection |

### Incomplete history

Mid-history tradebooks produce **orphan sells**. `process_csv` records a
`PartialBlock`, sets `cost_basis_complete=False` on affected rows, and surfaces
`incomplete_history` in the import result. `Transaction.objects.cost_basis()` excludes
incomplete rows from FIFO, realised P&L, and tax.

Importing an earlier file that supplies missing buys triggers `upgrade_chained_partials`
(same machinery as partial MF CAS history).

## Test fixtures

Sanitised Zerodha samples live in:

```
app/tests/fixtures/zerodha/          # backend e2e
frontend/src/test-fixtures/          # frontend wizard pipeline
```

`app/tests/tradebook_fixture.py` converts Zerodha CSV → canonical bytes (mirrors the
frontend mapping for pytest). End-to-end coverage:

- `app/tests/test_equity_tradebook_e2e.py` — import, orphan, golden CA, valuation trend
- `app/tests/test_import_csv.py` — unit-level CSV edge cases
- `app/tests/test_corporate_action_detect.py` — reconciliation suggestions

Do not commit raw broker exports or paths to private sample directories.

## OpenAPI

After changing import schemas, run `make openapi` and `make frontend-api`. The typed
client used by `TradebookImportView.vue` is generated from `openapi.json`.

## Related docs

- [User guide: importing a tradebook](../import-tradebook.md)
- [Valuation scheduler](valuation-scheduler.md) — day-wise series for ledger-backed equities
