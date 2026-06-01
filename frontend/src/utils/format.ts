/** Indian-locale number/currency formatting. Decimal API strings → display. */

const inr0 = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
})
const inr2 = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})
const pct2 = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})
const units4 = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 3,
  maximumFractionDigits: 4,
})

function toNumber(v: number | string | null | undefined): number {
  if (v === null || v === undefined) return 0
  return typeof v === 'number' ? v : Number(v)
}

/** ₹70,12,640 — whole rupees, Indian grouping. */
export function formatInr(v: number | string | null | undefined): string {
  return inr0.format(toNumber(v))
}

/** ₹3,368.42 — paise precision for ledgers. */
export function formatInrPaise(v: number | string | null | undefined): string {
  return inr2.format(toNumber(v))
}

/** Signed percent with two decimals, no % sign added (caller decides). */
export function formatPercent(v: number | string | null | undefined, signed = true): string {
  const n = toNumber(v)
  const body = pct2.format(Math.abs(n))
  if (!signed) return `${body}%`
  return `${n > 0 ? '+' : n < 0 ? '−' : ''}${body}%`
}

/** Mutual-fund units, 3–4 dp. */
export function formatUnits(v: number | string | null | undefined): string {
  return units4.format(toNumber(v))
}

/** ▲ / ▼ / · directional glyph for a delta. */
export function trendGlyph(v: number | string | null | undefined): '▲' | '▼' | '·' {
  const n = toNumber(v)
  return n > 0 ? '▲' : n < 0 ? '▼' : '·'
}

const dateFmt = new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })

/** ISO date/datetime → "30 May 2026"; null/invalid → em dash. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : dateFmt.format(d)
}

export { toNumber }
