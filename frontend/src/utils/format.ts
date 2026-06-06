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

const compact2 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 })

/**
 * Human-friendly INR for overview surfaces — ₹3.13Cr, ₹12.5L. Rounds amounts at
 * or above ₹1 lakh to L/Cr (≤2 decimals, trailing zeros trimmed); anything below
 * ₹1L falls back to the exact figure. For headlines, donut centres, chart
 * legends — never for ledgers, tables, or tax figures, which stay exact.
 */
export function formatInrCompact(v: number | string | null | undefined): string {
  const n = toNumber(v)
  const abs = Math.abs(n)
  if (abs < 1e5) return formatInr(n)
  const [div, suffix] = abs >= 1e7 ? [1e7, 'Cr'] : [1e5, 'L']
  return `${n < 0 ? '−' : ''}₹${compact2.format(abs / div)}${suffix}`
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

const nav4 = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 4, maximumFractionDigits: 4 })

/** A NAV / per-unit price to 4 dp, no currency symbol (e.g. 705.4251). */
export function formatNav(v: number | string | null | undefined): string {
  return nav4.format(toNumber(v))
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

const monthYearFmt = new Intl.DateTimeFormat('en-IN', { month: 'short', year: 'numeric' })

/** ISO date → "Jun 2025"; for trend axes where day-level detail is noise. */
export function formatMonthYear(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : monthYearFmt.format(d)
}

export { toNumber }
