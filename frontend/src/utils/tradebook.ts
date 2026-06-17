/**
 * Broker-tradebook → canonical-CSV mapping (pure, framework-free, unit-tested).
 *
 * A broker exports trades in its own column shape; the import wizard maps those
 * columns onto folioman's canonical fields, injects the per-import constants
 * (asset class + demat account), and emits the canonical CSV the backend
 * consumes. All the decisions a user could get wrong (which column is which,
 * required fields missing, a mistyped demat number) are decided here so they can
 * be tested without a DOM.
 */

/** A canonical field the user maps a file column onto. */
export interface CanonicalField {
  key: string
  label: string
  /** Must be mapped before the import can proceed. */
  required: boolean
  /** Header substrings (normalized) that auto-map onto this field. */
  aliases: string[]
}

// Identity (`name`/`symbol`/`isin`) is handled as a group: at least one is
// required (see `mappingErrors`), but none individually. `security_type`,
// `folio_number`, and `broker` are per-import constants, not mapped columns.
export const CANONICAL_FIELDS: CanonicalField[] = [
  { key: 'date', label: 'Date', required: true, aliases: ['date', 'tradedate', 'transactiondate'] },
  {
    key: 'transaction_type',
    label: 'Type (buy/sell)',
    required: true,
    aliases: ['type', 'tradetype', 'transactiontype', 'txntype', 'buysell', 'side'],
  },
  {
    key: 'units',
    label: 'Units / quantity',
    required: true,
    aliases: ['units', 'quantity', 'qty', 'shares'],
  },
  {
    key: 'price',
    label: 'Price',
    required: true,
    aliases: ['price', 'rate', 'tradeprice', 'avgprice', 'nav'],
  },
  {
    key: 'symbol',
    label: 'Symbol / ticker',
    required: false,
    aliases: ['symbol', 'ticker', 'tradingsymbol', 'scrip'],
  },
  { key: 'isin', label: 'ISIN', required: false, aliases: ['isin'] },
  {
    key: 'name',
    label: 'Security name',
    required: false,
    aliases: ['name', 'securityname', 'scripname', 'company', 'instrument'],
  },
  {
    key: 'amount',
    label: 'Amount',
    required: false,
    aliases: ['amount', 'value', 'tradevalue', 'netamount'],
  },
  { key: 'fees', label: 'Fees / STT', required: false, aliases: ['fees', 'stt', 'tax'] },
  { key: 'stamp_duty', label: 'Stamp duty', required: false, aliases: ['stampduty', 'stamp'] },
  {
    key: 'brokerage',
    label: 'Brokerage',
    required: false,
    aliases: ['brokerage', 'commission', 'brokeragecharges'],
  },
  { key: 'currency', label: 'Currency', required: false, aliases: ['currency', 'ccy'] },
  {
    key: 'source_ref',
    label: 'Trade ID',
    required: false,
    aliases: ['sourceref', 'tradeid', 'orderid', 'ref'],
  },
]

/** Column order of the emitted canonical CSV (matches the backend contract). */
export const CANONICAL_COLUMNS = [
  'security_type',
  'name',
  'symbol',
  'isin',
  'date',
  'transaction_type',
  'units',
  'price',
  'amount',
  'fees',
  'stamp_duty',
  'brokerage',
  'currency',
  'source_ref',
  'folio_number',
  'broker',
]

/** A column→canonical mapping: canonical key -> file header (or '' = unmapped). */
export type Mapping = Record<string, string>

const REQUIRED_KEYS = CANONICAL_FIELDS.filter((f) => f.required).map((f) => f.key)
const IDENTITY_KEYS = ['name', 'symbol', 'isin']

/** Normalize a header for fuzzy matching: lowercase, drop non-alphanumerics. */
function normalizeHeader(header: string): string {
  return header.toLowerCase().replace(/[^a-z0-9]/g, '')
}

/**
 * Guess a mapping from a file's headers, matching each canonical field's aliases
 * against the normalized headers. First header to match a field wins; a header
 * already claimed by an earlier field isn't reused.
 */
export function autoDetectMapping(headers: string[]): Mapping {
  const normalized = headers.map((h) => ({ raw: h, norm: normalizeHeader(h) }))
  const taken = new Set<string>()
  const mapping: Mapping = {}
  for (const field of CANONICAL_FIELDS) {
    const hit = normalized.find((h) => !taken.has(h.raw) && field.aliases.includes(h.norm))
    if (hit) {
      mapping[field.key] = hit.raw
      taken.add(hit.raw)
    } else {
      mapping[field.key] = ''
    }
  }
  return mapping
}

/**
 * Blocking problems with a mapping: every required field must be mapped, and at
 * least one identity field (name/symbol/isin) — otherwise a row has no security
 * to attach to and no name to satisfy the backend. Returns human-readable
 * messages (empty = ready to import).
 */
export function mappingErrors(mapping: Mapping): string[] {
  const errors: string[] = []
  for (const key of REQUIRED_KEYS) {
    if (!mapping[key]) {
      const field = CANONICAL_FIELDS.find((f) => f.key === key)
      errors.push(`Map a column to “${field?.label ?? key}”.`)
    }
  }
  if (!IDENTITY_KEYS.some((k) => mapping[k])) {
    errors.push('Map a column to the symbol, ISIN, or security name.')
  }
  return errors
}

/** A real demat account number: 16-digit CDSL BO ID or NSDL "IN" + 14 digits. */
const DEMAT_NUMBER_RE = /^(?:\d{16}|IN\d{14})$/

/** Whether `value` looks like a demat account number (mirrors the backend check). */
export function isValidDematNumber(value: string): boolean {
  return DEMAT_NUMBER_RE.test(value.trim().toUpperCase())
}

const NUMERIC_FIELD_KEYS = new Set([
  'units',
  'price',
  'amount',
  'fees',
  'stamp_duty',
  'brokerage',
])

/**
 * Strip locale formatting from a numeric cell (thousands separators, currency
 * glyphs) so the backend ``Decimal()`` parse accepts XLSX display strings.
 */
export function normalizeDecimalCell(value: string): string {
  const s = value.trim()
  if (!s) return ''
  if (/^-?\d+(\.\d+)?$/.test(s)) return s
  const neg = s.startsWith('-')
  const body = s.replace(/[₹$€£,\s]/g, '').replace(/[^\d.]/g, '')
  if (!body) return s
  return neg && !body.startsWith('-') ? `-${body}` : body
}

export interface CanonicalOptions {
  /** Demat account number (BO ID) the whole import attaches to. */
  folioNumber: string
  /** Broker name for the demat folio. */
  broker: string
  /** Asset class injected as the constant `security_type` (default 'equity'). */
  securityType?: string
}

/**
 * Project parsed file rows onto canonical rows via `mapping`, injecting the
 * per-import constants. `name` falls back to the symbol then the ISIN when the
 * file carries no name column, so the backend's name requirement is met before
 * the ISIN→name resolver (E5) lands. Rows whose mapped values are all blank are
 * dropped (trailing empty lines from spreadsheets).
 */
export function buildCanonicalRows(
  fileRows: Record<string, string>[],
  mapping: Mapping,
  options: CanonicalOptions,
): Record<string, string>[] {
  const securityType = options.securityType ?? 'equity'
  const out: Record<string, string>[] = []
  for (const fileRow of fileRows) {
    const row: Record<string, string> = {}
    for (const field of CANONICAL_FIELDS) {
      const header = mapping[field.key]
      let cell = header ? (fileRow[header] ?? '').trim() : ''
      if (cell && NUMERIC_FIELD_KEYS.has(field.key)) {
        cell = normalizeDecimalCell(cell)
      }
      row[field.key] = cell
    }
    // Skip a row that mapped to nothing (blank spreadsheet tail).
    if (CANONICAL_FIELDS.every((f) => !row[f.key])) continue
    if (!row.name) row.name = row.symbol || row.isin || ''
    row.security_type = securityType
    row.folio_number = options.folioNumber
    row.broker = options.broker
    out.push(row)
  }
  return out
}
