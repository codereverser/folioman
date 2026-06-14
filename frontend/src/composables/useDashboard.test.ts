import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// Route api.GET by path so both useDashboard (summary + value-series) and the
// integrity store it reads resolve against fixtures.
const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/api/client', () => ({ api: { GET: get } }))
// The composable toasts via the ui store; stub it.
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
  category_mix: [
    { label: 'Equity', value_inr: '6000' },
    { label: 'Debt', value_inr: '1500' },
  ],
  amc_mix: [
    { label: 'HDFC MF', value_inr: '4000' },
    { label: 'Axis MF', value_inr: '3500' },
  ],
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
  holdings: [
    {
      security_id: 1,
      name: 'Fund A',
      security_type: 'mf',
      amc: 'HDFC Mutual Fund',
      category: 'Equity',
      units: '100',
      value_inr: '7500',
      invested_inr: '5000',
      return_pct: 0.65,
      xirr: 0.21,
    },
    {
      security_id: 2,
      name: 'RELIANCE INDUSTRIES LIMITED EQUITY SHARES',
      security_type: 'equity',
      symbol: 'RELIANCE',
      units: '10',
      value_inr: '14000',
      invested_inr: '10000',
      latest_nav: '1400',
      return_pct: 0.4,
      day_change_inr: '120',
      day_change_pct: 0.0086,
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
    setActivePinia(createPinia())
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
    expect(top).toMatchObject({
      securityId: 1,
      name: 'Fund A',
      assetClass: 'Mutual funds',
      units: 100,
    })
    expect(top.integrity).toBe('full_history')
    expect(top.returnPct).toBeCloseTo(65) // 0.65 fraction → percent
  })

  it('maps the per-fund list for the MF breakdown (grouping keys, XIRR, integrity)', async () => {
    const { summary } = useDashboard(ref(1))
    await flush()
    await flush()

    expect(summary.value.funds).toHaveLength(1)
    const fund = summary.value.funds[0]
    expect(fund).toMatchObject({ securityId: 1, name: 'Fund A', category: 'Equity' })
    expect(fund.amc).toBe('HDFC') // AMC boilerplate trimmed
    expect(fund.xirr).toBeCloseTo(21) // 0.21 fraction → percent
    expect(fund.returnPct).toBeCloseTo(65)
    expect(fund.gain).toBe(2500) // value 7500 − invested 5000, for the movers strip
    expect(fund.integrity).toBe('full_history') // joined from the integrity store
  })

  it('maps equity holdings into the stocks list (ticker, price, avg cost, 1D)', async () => {
    const { summary } = useDashboard(ref(1))
    await flush()
    await flush()

    expect(summary.value.stocks).toHaveLength(1) // only equities; the MF stays out
    expect(summary.value.stockTotal).toBe(14000)
    const stock = summary.value.stocks[0]
    expect(stock).toMatchObject({ securityId: 2, symbol: 'RELIANCE', units: 10, value: 14000 })
    expect(stock.price).toBe(1400) // latest_nav = current price
    expect(stock.avgCost).toBe(1000) // invested 10000 / 10 shares
    expect(stock.gain).toBe(4000) // value 14000 − invested 10000
    expect(stock.returnPct).toBeCloseTo(40)
    expect(stock.dayChangeAmount).toBe(120)
    expect(stock.dayChangePercent).toBeCloseTo(0.86)
    expect(summary.value.funds).toHaveLength(1) // MF list unaffected
  })

  it('maps category + AMC allocation breakdowns into coloured slices', async () => {
    const { summary } = useDashboard(ref(1))
    await flush()
    await flush()

    const byCat = summary.value.allocationByCategory
    expect(byCat.map((s) => s.name)).toEqual(['Equity', 'Debt'])
    expect(byCat[0]).toMatchObject({ value: 6000 })
    expect(byCat.every((s) => !!s.color)).toBe(true)

    const byAmc = summary.value.allocationByAmc
    // AMC boilerplate suffixes are trimmed for the legend ("HDFC MF" → "HDFC").
    expect(byAmc.map((s) => s.name)).toEqual(['HDFC', 'Axis'])
    expect(byAmc.every((s) => !!s.color)).toBe(true)
  })

  it('caps the AMC breakdown to the top 6 and folds the tail into "Others"', async () => {
    // 9 buckets, value-desc 900..100; top 6 kept, tail (300+200+100) → Others.
    const many = Array.from({ length: 9 }, (_, i) => ({
      label: `AMC ${i}`,
      value_inr: String(900 - i * 100),
    }))
    get.mockImplementation((path: string) =>
      path.endsWith('/summary')
        ? Promise.resolve({ data: { ...SUMMARY, amc_mix: many } })
        : routeGet(path),
    )
    const { summary } = useDashboard(ref(1))
    await flush()
    await flush()

    const byAmc = summary.value.allocationByAmc
    expect(byAmc).toHaveLength(7)
    expect(byAmc[6]).toMatchObject({ name: 'Others', value: 600 })
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
