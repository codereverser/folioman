import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref } from 'vue'

// Route api.GET by path so both useDashboard (summary + value-series) and the
// integrity composable it wraps resolve against fixtures.
const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/api/client', () => ({ api: { GET: get } }))
// The composable toasts via the ui store; stub it (no Pinia in this unit test).
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ notify: vi.fn() }) }))

import { useDashboard } from './useDashboard'

const SUMMARY = {
  total_inr: '7500',
  holdings_count: 1,
  tax_ready_count: 1,
  needs_attention_count: 0,
  snapshot_count: 0,
  stale_count: 0,
  last_import_at: null,
  day_change_inr: '75', // prior value 7425 → +1.0101%
  xirr: 0.1849,
  as_of: '2025-06-01',
  asset_mix: [{ security_type: 'mf', value_inr: '7500' }],
  top_holdings: [
    {
      security_id: 1,
      name: 'Fund A',
      security_type: 'mf',
      units: '100',
      value_inr: '7500',
      return_pct: 0.65,
    },
  ],
}
// A leading all-zero point (before the first holding) that must be trimmed.
const SERIES = [
  { date: '2025-01-01', value_inr: '0', invested_inr: '0', stale: false },
  { date: '2025-02-01', value_inr: '1200', invested_inr: '1000', stale: false },
  { date: '2025-06-01', value_inr: '7500', invested_inr: '1000', stale: false },
]
const INTEGRITY = [
  {
    security: { id: 1, name: 'Fund A', isin: 'INF0001', symbol: '', security_type: 'mf' },
    folio: { id: 1, number: 'F1', broker: '', folio_type: 'mf' },
    status: 'full_history',
    tax_safe: true,
    units_from_holdings: null,
    units_from_transactions: '100',
    issues: [],
    last_reconciled_at: null,
  },
]

function routeGet(path: string) {
  if (path.endsWith('/summary')) return Promise.resolve({ data: SUMMARY })
  if (path.endsWith('/value-series')) return Promise.resolve({ data: { points: SERIES } })
  if (path.endsWith('/integrity')) return Promise.resolve({ data: INTEGRITY })
  return Promise.resolve({ data: null })
}

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('useDashboard', () => {
  beforeEach(() => {
    get.mockReset()
    get.mockImplementation((path: string) => routeGet(path))
  })

  it('maps the live summary + series into the dashboard view-model', async () => {
    const { summary } = useDashboard(ref(1))
    await flush()
    await flush()

    const s = summary.value
    expect(s.netWorth).toBe(7500)
    // invested = FIFO cost basis from the latest series point.
    expect(s.invested).toBe(1000)
    expect(s.totalReturnAmount).toBe(6500)
    expect(s.totalReturnPercent).toBe(650)
    // xirr fraction → percent for the card.
    expect(s.xirr).toBeCloseTo(18.49)
    expect(s.asOf).toContain('as of')
    // day-change: absolute from the API, percent derived against the prior value.
    expect(s.dayChangeAmount).toBe(75)
    expect(s.dayChangePercent).toBeCloseTo((75 / 7425) * 100)
  })

  it('trims the leading all-zero stretch from the value series', async () => {
    const { summary } = useDashboard(ref(1))
    await flush()
    await flush()

    expect(summary.value.valueSeries).toHaveLength(2)
    expect(summary.value.valueSeries[0].date).toBe('2025-02-01')
  })

  it('builds allocation + top holdings and joins per-holding integrity', async () => {
    const { summary } = useDashboard(ref(1))
    await flush()
    await flush()

    expect(summary.value.allocation[0]).toMatchObject({ name: 'Mutual funds', value: 7500 })
    expect(summary.value.allocation[0].color).toBeTruthy()
    const top = summary.value.topHoldings[0]
    expect(top).toMatchObject({ securityId: 1, name: 'Fund A', assetClass: 'Mutual funds', units: 100 })
    expect(top.integrity).toBe('full_history')
    expect(top.returnPct).toBeCloseTo(65) // 0.65 fraction → percent
  })

  it('shows null xirr (not 0) when the backend has no rate', async () => {
    get.mockImplementation((path: string) => {
      if (path.endsWith('/summary')) return Promise.resolve({ data: { ...SUMMARY, xirr: null } })
      return routeGet(path)
    })
    const { summary } = useDashboard(ref(1))
    await flush()
    await flush()

    expect(summary.value.xirr).toBeNull()
  })
})
