# Zerodha tradebook fixtures (sanitised)

Redacted samples derived from a real Zerodha Console equity tradebook export.
Used by `test_equity_tradebook_e2e.py` and the frontend fixture tests.

**Sanitisation:** filenames carry no client or account codes; `trade_id` and
`order_id` values are replaced with synthetic `T…` / `O…` ids. Trade symbols,
ISINs, dates, quantities, and prices are unchanged (public market data).

Do not commit raw exports or paths to private sample directories.
