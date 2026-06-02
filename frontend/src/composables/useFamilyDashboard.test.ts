import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// Route api.GET by path: the aggregate + value-series, the roster (families /
// investors) for the member list, and each member's summary for their total.
const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/api/client', () => ({ api: { GET: get } }))

import { useFamilyDashboard } from './useFamilyDashboard'

const AGG = {
  family_id: 1,
  as_of: '2025-06-01',
  investor_count: 2,
  folio_count: 4,
  total_inr: '8000',
  stale_count: 0,
  day_change_inr: '80', // prior 7920 → ~1.0101%
  xirr: 0.2,
  asset_mix: [{ security_type: 'mf', value_inr: '8000' }],
  top_holdings: [
    {
      security_id: 1,
      name: 'Fund A',
      security_type: 'mf',
      units: '100',
      value_inr: '8000',
      return_pct: 0.6,
    },
  ],
}
const SERIES = [
  { date: '2025-01-01', value_inr: '0', invested_inr: '0', stale: false }, // trimmed
  { date: '2025-06-01', value_inr: '8000', invested_inr: '5000', stale: false },
]
const INVESTORS = [
  { id: 10, name: 'Rajesh', family_id: 1 },
  { id: 11, name: 'Priya', family_id: 1 },
  { id: 20, name: 'Solo', family_id: null },
]

function routeGet(path: string, init?: { params?: { path?: { investor_id?: number } } }) {
  if (path === '/api/families/') return Promise.resolve({ data: [{ id: 1, name: 'Sharma' }] })
  if (path === '/api/investors/') return Promise.resolve({ data: INVESTORS })
  if (path.endsWith('/aggregate')) return Promise.resolve({ data: AGG })
  if (path.endsWith('/value-series')) return Promise.resolve({ data: { points: SERIES } })
  if (path.endsWith('/summary')) {
    const id = init?.params?.path?.investor_id
    return Promise.resolve({ data: { total_inr: id === 10 ? '5000' : '3000' } })
  }
  return Promise.resolve({ data: null })
}

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('useFamilyDashboard', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    get.mockReset()
    get.mockImplementation((path: string, init: never) => routeGet(path, init))
  })

  it('maps the combined aggregate into the family view-model', async () => {
    const { summary } = useFamilyDashboard(ref(1))
    await flush()
    await flush()

    const s = summary.value
    expect(s.total).toBe(8000)
    expect(s.investorCount).toBe(2)
    expect(s.folioCount).toBe(4)
    expect(s.xirr).toBeCloseTo(20)
    expect(s.dayChangeAmount).toBe(80)
    expect(s.dayChangePercent).toBeCloseTo((80 / 7920) * 100)
    expect(s.allocation[0]).toMatchObject({ name: 'Mutual funds', value: 8000 })
    expect(s.topHoldings[0]).toMatchObject({ name: 'Fund A', returnPct: 60 })
  })

  it('trims the leading all-zero point from the combined series', async () => {
    const { summary } = useFamilyDashboard(ref(1))
    await flush()
    await flush()

    expect(summary.value.valueSeries).toHaveLength(1)
    expect(summary.value.valueSeries[0].date).toBe('2025-06-01')
  })

  it('lists the family members with their individual totals', async () => {
    const { members } = useFamilyDashboard(ref(1))
    await flush()
    await flush()
    await flush()

    // Only family 1's investors (10, 11) — the solo investor (20) is excluded.
    expect(members.value.map((m) => m.id).sort()).toEqual([10, 11])
    const rajesh = members.value.find((m) => m.id === 10)
    expect(rajesh).toMatchObject({ name: 'Rajesh', totalInr: 5000 })
  })
})
