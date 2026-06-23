Sanitised broker tradebook samples for frontend vitest.

- `tradebook-zerodha-2024.csv` — multi-fill Zerodha CSV (mirrors `app/tests/fixtures/zerodha/`).
- `tradebook-zerodha-minimal.xlsx` — minimal single-sheet workbook without Console preamble rows.

Do not commit raw broker exports (they may contain client IDs in banner rows).
