import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/api/client', () => ({ api: { GET: get } }))

import { useIncome } from './useIncome'

const INTEGRITY = [
  {
    security: { id: 1, name: 'Reliance', isin: 'X', symbol: '', security_type: 'equity' },
    folio: { id: 1, number: 'F1', broker: '', folio_type: 'demat' },
    status: 'snapshot_only',
    tax_safe: false,
    units_from_holdings: '5',
    units_from_transactions: null,
    issues: [],
    last_reconciled_at: null,
  },
]

const REPORT = {
  fy: '2024-25',
  accrued_total: '500.00',
  received_total: '500.00',
  dividend_quarters: [{ label: 'Upto 15/6', amount: '500.00' }],
  groups: [
    {
      kind: 'dividend',
      basis: 'received',
      accrued_total: '500.00',
      received_total: '500.00',
      rows: [
        {
          security_id: 1,
          name: 'Reliance',
          asset_type: 'equity',
          kind: 'dividend',
          accrued: '500.00',
          received: '500.00',
          yield_on_cost: null,
        },
      ],
    },
  ],
  disclaimer: 'Not tax advice.',
}
const BY_FY = [
  { fy: '2023-24', dividends: '100.00', interest: '0.00' },
  { fy: '2024-25', dividends: '500.00', interest: '0.00' },
]

function routeGet(path: string) {
  if (path.endsWith('/reports/income')) return Promise.resolve({ data: REPORT })
  if (path.endsWith('/reports/income-by-fy')) return Promise.resolve({ data: BY_FY })
  if (path.endsWith('/integrity')) return Promise.resolve({ data: INTEGRITY })
  return Promise.resolve({ data: null })
}
const flush = () => new Promise((r) => setTimeout(r, 0))

describe('useIncome', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    get.mockReset()
    get.mockImplementation((path: string) => routeGet(path))
  })

  it('defaults FY, kind=both, basis=accrued', () => {
    const x = useIncome(ref(1))
    expect(x.fy.value).toMatch(/^\d{4}-\d{2}$/)
    expect(x.kind.value).toBe('both')
    expect(x.basis.value).toBe('accrued')
  })

  it('builds the report + by-FY series', async () => {
    const x = useIncome(ref(1))
    await x.build()
    await x.loadSeries() // the chart series is FY-independent, loaded separately
    expect(x.built.value).toBe(true)
    expect(x.dividendsTotal.value).toBe(500)
    expect(x.byFy.value).toHaveLength(2)
    expect(x.visibleGroups.value).toHaveLength(1)
  })

  it('changing FY refetches only the report, not the chart series', async () => {
    const x = useIncome(ref(1))
    await x.loadSeries()
    await x.build()
    get.mockClear()
    x.fy.value = '2023-24'
    await x.build()
    const paths = get.mock.calls.map((c) => c[0] as string)
    expect(paths.some((p) => p.endsWith('/reports/income'))).toBe(true)
    expect(paths.some((p) => p.endsWith('/reports/income-by-fy'))).toBe(false)
  })

  it('kind filter recomputes without a refetch', async () => {
    const x = useIncome(ref(1))
    await x.build()
    get.mockClear()
    x.kind.value = 'interest' // no interest rows → nothing shown, no fetch
    expect(x.visibleGroups.value).toHaveLength(0)
    expect(x.shownTotal.value).toBe(0)
    x.kind.value = 'dividend'
    expect(x.visibleGroups.value).toHaveLength(1)
    expect(get).not.toHaveBeenCalled()
  })

  it('basis toggle recomputes totals without a refetch', async () => {
    const x = useIncome(ref(1))
    await x.build()
    get.mockClear()
    x.basis.value = 'received'
    expect(x.shownTotal.value).toBe(500) // dividends: received == accrued
    expect(get).not.toHaveBeenCalled()
  })

  it('flags securities that are not fully reconciled', async () => {
    const x = useIncome(ref(1))
    await x.build()
    await flush() // integrity loads after the report
    expect(x.isIncomplete(1)).toBe(true) // snapshot_only, not tax-safe
    expect(x.isIncomplete(2)).toBe(false)
  })

  it('fails soft when the report request errors', async () => {
    get.mockImplementation((path: string) =>
      path.endsWith('/reports/income')
        ? Promise.resolve({ error: { detail: 'bad fy' } })
        : routeGet(path),
    )
    const x = useIncome(ref(1))
    await x.build()
    expect(x.report.value).toBeNull()
    expect(x.error.value).not.toBeNull()
    expect(x.byFy.value).toEqual([])
  })
})
