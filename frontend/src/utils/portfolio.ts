// Shared helpers for the investor + family dashboards: numeric coercion,
// security-type display labels/colours (so donut slices stay consistent across
// both pages), and the value-series range windows.

export type RangeKey = '1M' | '3M' | '6M' | '1Y' | '3Y' | '5Y' | 'All'

/** Coerce a Decimal-as-string (or number) from the API to a finite number. */
export function num(v: string | number | null | undefined): number {
  const n = typeof v === 'string' ? Number(v) : (v ?? 0)
  return Number.isFinite(n) ? n : 0
}

// Display label, a fixed colour, and a quantity noun per security type. MF is the
// common case; the rest get distinct asset-class colours so a mixed portfolio reads
// clearly. `unit` labels a holding's quantity in lists (units / shares / coins / …).
export const ASSET_META: Record<string, { label: string; color: string; unit: string }> = {
  mf: { label: 'Mutual funds', color: 'var(--fm-asset-equity)', unit: 'units' },
  equity: { label: 'Stocks', color: 'var(--fm-asset-intl)', unit: 'shares' },
  etf: { label: 'ETFs', color: 'var(--fm-asset-gold)', unit: 'units' },
  bond: { label: 'Bonds', color: 'var(--fm-asset-debt)', unit: 'units' },
  fd: { label: 'Fixed deposits', color: 'var(--fm-asset-cash)', unit: '' },
  crypto: { label: 'Crypto', color: 'var(--fm-asset-crypto)', unit: 'coins' },
  foreign_equity: { label: 'International', color: 'var(--fm-asset-realestate)', unit: 'shares' },
}
export function assetLabel(securityType: string): string {
  return ASSET_META[securityType]?.label ?? securityType
}
/** Asset-class swatch colour for a security type; neutral when unknown. */
export function assetColor(securityType: string): string {
  return ASSET_META[securityType]?.color ?? 'var(--fm-asset-cash)'
}
/** Quantity noun (units / shares / coins …) for a security type; '' when n/a. */
export function assetUnit(securityType: string): string {
  return ASSET_META[securityType]?.unit ?? 'units'
}

// Equity vs Debt slices reuse the semantic asset-class colours (equity = brand
// teal, debt = blue); anything else falls back to neutral slate.
const CATEGORY_COLOR: Record<string, string> = {
  Equity: 'var(--fm-asset-equity)',
  Debt: 'var(--fm-asset-debt)',
  Hybrid: 'var(--fm-asset-gold)',
}
export function categoryColor(label: string): string {
  return CATEGORY_COLOR[label] ?? 'var(--fm-asset-cash)'
}

// Ordered, max-separation ramp (DESIGN-SYSTEM §2.6) for many-series sub-category
// breakdowns like per-AMC slices. Excludes pure gain-green / loss-red.
export const SEQUENTIAL_RAMP = [
  '#2DD4BF',
  '#38BDF8',
  '#818CF8',
  '#C084FC',
  '#FBBF24',
  '#E879F9',
  '#67E8F9',
  '#A3E635',
  '#FB7185',
] as const
/** Cycle the ramp for an arbitrary number of sub-category (e.g. AMC) slices. */
export function rampColor(index: number): string {
  return SEQUENTIAL_RAMP[index % SEQUENTIAL_RAMP.length]
}

// Trim boilerplate AMC suffixes ("HDFC Mutual Fund" → "HDFC") so the donut legend
// stays compact. Falls back to the full name if trimming would empty it.
export function shortAmc(name: string): string {
  const trimmed = name
    .replace(/\s+(mutual fund|mf|asset management company|asset management|amc)\s*$/i, '')
    .trim()
  return trimmed || name
}

function monthsAgo(n: number): string {
  const d = new Date()
  d.setMonth(d.getMonth() - n)
  return d.toISOString().slice(0, 10)
}

// Each range maps to a value-series window + sampling granularity. Granularity
// scales with the window so point counts stay readable: short ranges plot every
// day, mid ranges thin to weekly, multi-year ranges to monthly. "All" reaches
// back far enough to cover any real portfolio; leading all-zero points (before
// the first holding) are trimmed by the consumer.
export const RANGES: Record<
  RangeKey,
  { from: () => string; granularity: 'daily' | 'weekly' | 'monthly' }
> = {
  '1M': { from: () => monthsAgo(1), granularity: 'daily' },
  '3M': { from: () => monthsAgo(3), granularity: 'daily' },
  '6M': { from: () => monthsAgo(6), granularity: 'weekly' },
  '1Y': { from: () => monthsAgo(12), granularity: 'weekly' },
  '3Y': { from: () => monthsAgo(36), granularity: 'monthly' },
  '5Y': { from: () => monthsAgo(60), granularity: 'monthly' },
  All: { from: () => '2000-01-01', granularity: 'monthly' },
}

/** The range toggle's options, in display order — same on every dashboard. */
export const RANGE_OPTIONS: { label: RangeKey; value: RangeKey }[] = (
  Object.keys(RANGES) as RangeKey[]
).map((k) => ({ label: k, value: k }))

/** Human phrasing of a range for "value gained/lost over …" captions. */
export const RANGE_LABEL: Record<RangeKey, string> = {
  '1M': 'past month',
  '3M': 'past 3 months',
  '6M': 'past 6 months',
  '1Y': 'past year',
  '3Y': 'past 3 years',
  '5Y': 'past 5 years',
  All: 'all time',
}

/**
 * Value change from a window's start to the series' end (price move, not cash
 * flow) — the delta shown under a hero number for the selected chart range.
 * Skips leading zero-value days (pre-holding stretch); null when undeterminable.
 */
export function windowChange(
  points: { date: string; current: number }[],
  from?: string | null,
): { amount: number; pct: number } | null {
  if (points.length < 2) return null
  const inWindow = from ? points.filter((p) => p.date >= from) : points
  const startPt = inWindow.find((p) => p.current > 0) ?? inWindow[0]
  const start = startPt?.current ?? 0
  const end = points[points.length - 1].current
  if (!start) return null
  return { amount: end - start, pct: ((end - start) / start) * 100 }
}
