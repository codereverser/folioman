// Shared helpers for the investor + family dashboards: numeric coercion,
// security-type display labels/colours (so donut slices stay consistent across
// both pages), and the value-series range windows.

export type RangeKey = '6M' | '1Y' | 'All'

/** Coerce a Decimal-as-string (or number) from the API to a finite number. */
export function num(v: string | number | null | undefined): number {
  const n = typeof v === 'string' ? Number(v) : (v ?? 0)
  return Number.isFinite(n) ? n : 0
}

// Display label + a fixed colour per security type. MF is the common case; the
// rest get distinct asset-class colours so a mixed portfolio reads clearly.
export const ASSET_META: Record<string, { label: string; color: string }> = {
  mf: { label: 'Mutual funds', color: 'var(--fm-asset-equity)' },
  equity: { label: 'Stocks', color: 'var(--fm-asset-intl)' },
  etf: { label: 'ETFs', color: 'var(--fm-asset-gold)' },
  bond: { label: 'Bonds', color: 'var(--fm-asset-debt)' },
  fd: { label: 'Fixed deposits', color: 'var(--fm-asset-cash)' },
  crypto: { label: 'Crypto', color: 'var(--fm-asset-crypto)' },
  foreign_equity: { label: 'International', color: 'var(--fm-asset-realestate)' },
}
export function assetLabel(securityType: string): string {
  return ASSET_META[securityType]?.label ?? securityType
}

function monthsAgo(n: number): string {
  const d = new Date()
  d.setMonth(d.getMonth() - n)
  return d.toISOString().slice(0, 10)
}

// Each range maps to a value-series window + sampling granularity. "All" reaches
// back far enough to cover any real portfolio; leading all-zero points (before
// the first holding) are trimmed by the consumer.
export const RANGES: Record<RangeKey, { from: () => string; granularity: 'daily' | 'weekly' | 'monthly' }> = {
  '6M': { from: () => monthsAgo(6), granularity: 'monthly' },
  '1Y': { from: () => monthsAgo(12), granularity: 'monthly' },
  All: { from: () => '2000-01-01', granularity: 'monthly' },
}
