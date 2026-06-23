# Importing a broker stock tradebook

Folioman can build a **full transaction history** for your listed equities from a
broker **tradebook** export (CSV or Excel). That unlocks FIFO cost basis, realised
gains, capital-gains worksheets, and — when your history is complete — inclusion
in the portfolio trend line.

This is separate from:

- **MF CAS** — mutual-fund transaction history from a CAMS/Kfintech PDF.
- **Demat eCAS** — a holdings snapshot (NSDL/CDSL) with no trade history.

You typically use **both** a tradebook and an eCAS: the tradebook supplies buys and
sells; the eCAS anchors current demat holdings for reconciliation.

## Before you start

1. **Pick the investor** the trades belong to (tradebooks carry no PAN — unlike a CAS).
2. **Know your demat account number** (16-digit CDSL BO ID, or NSDL `IN` + 14 digits).
   If you have already imported an eCAS, Folioman can offer your demat accounts in a
   picker; otherwise type the number and broker name.
3. Export an **equity (EQ) delivery** tradebook from your broker. F&O, intraday, and
   commodity segments are not supported in this release.

## Exporting from Zerodha Console (example)

These steps are for Zerodha; other brokers publish a similar trade list under a
different menu name.

1. Log in to [Zerodha Console](https://console.zerodha.com/).
2. Go to **Reports → Tradebook**.
3. Choose **Equity**, pick the financial year (or date range), and download **CSV**
   or **Excel**.

   **Tip:** CSV is simpler — Zerodha’s Excel export includes banner rows above the
   column headers. The wizard expects the first row of the sheet to be headers; if
   auto-detect looks wrong on an `.xlsx` file, switch to CSV or re-export as CSV.

Zerodha’s file columns look like:

`symbol`, `isin`, `trade_date`, `trade_type`, `quantity`, `price`, `trade_id`, …

Other brokers use different header names — that is expected.

## Importing in Folioman

1. Open **Import** and choose **Stock tradebook**.
2. Select the investor and upload the CSV/XLSX file.
3. **Map columns** — match your file’s headers to Folioman’s fields (date, buy/sell,
   quantity, price, symbol or ISIN, and trade ID if present). The wizard auto-detects
   common Zerodha headers; adjust anything it missed.
4. Confirm the **demat account** this file belongs to.
5. Review the preview and import.

Folioman converts your mapping into an internal canonical format and imports each
fill as a ledger row. Re-importing the same file is safe: identical rows are skipped.

## Column mapping — what it means

Brokers do not share one standard export shape. Rather than hard-coding Zerodha (or
any single broker), Folioman asks you to **map once per file**:

| You map to… | Typical Zerodha column | Why it matters |
|---|---|---|
| Date | `trade_date` | FIFO ordering |
| Type (buy/sell) | `trade_type` | Ledger direction |
| Units / quantity | `quantity` | Share count per fill |
| Price | `price` | Execution price |
| Symbol or ISIN | `symbol`, `isin` | Security identity (ISIN preferred) |
| Trade ID | `trade_id` | Keeps duplicate-looking fills distinct |

The tradebook rarely includes the company name. The wizard fills a provisional name
from the symbol; Folioman then resolves the authoritative name from the ISIN database.

**Trade ID is important on Zerodha exports:** one order can produce several fills with
the same date, symbol, quantity, and price. Without `trade_id`, two genuine fills
could collapse into one row.

## After import — what the status means

### Full history

Buys and sells reconcile cleanly: every sell is covered by earlier buys in the file
(or an earlier import). These securities can show realised P&L, tax worksheets, and
enter the **day-wise trend** (when priced).

### Incomplete history

Broker exports often start around **FY 2017–18** (~2016 calendar). If you sold shares
you bought before the export window, the file contains **orphan sells** — sells with
no matching buy inside Folioman.

Folioman **does not guess** missing purchase prices. Instead it:

- Imports the rows it can see.
- Marks the security **incomplete history**.
- Excludes those lots from realised gains and tax until you supply earlier buys.

**How to fix it:** export and import an **earlier-period tradebook** that includes the
missing purchases. Folioman merges idempotently and upgrades the ledger when the
chain becomes solvent.

### Reconciled vs mismatch (with eCAS)

If you also import a demat eCAS, Folioman compares ledger net units to the statement
holding per security:

- **Reconciled** — ledger matches the eCAS anchor.
- **Mismatch** — units differ (corporate action, missing trades, or incomplete history).
  Open **Integrity** to review suggestions (e.g. bonus/split) or acknowledge a known gap.

Corporate actions such as bonuses and splits are **suggested**, not applied silently.
You confirm them on the Integrity page.

## Tips

- Import **oldest tradebooks first** when catching up multi-year history.
- Keep **EQ delivery** trades only; strip or ignore other segments before upload.
- Use the same **demat account number** as on your eCAS so folios line up.
- Dividends are **not** in the tradebook — Folioman attributes them separately from
  exchange corporate-action data when your equity ledger is complete.

## See also

- [Developer note: canonical tradebook format](developer/tradebook-import.md)
- [Self-host with Docker](install-docker.md) / [Desktop build](../BUILD.md)
