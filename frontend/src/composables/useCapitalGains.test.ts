import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/api/client', () => ({ api: { GET: get, POST: post } }))

import { useCapitalGains } from './useCapitalGains'

const CG = {
  fy: '2024-25',
  stcg_total: '200.00',
  ltcg_total: '1000.00',
  rows: [
    {
      security_id: 1,
      name: 'A',
      isin: 'X',
      units: '10',
      sale_value: '2000.00',
      cost: '1000.00',
      gain: '1000.00',
      term: 'long',
      acquired_on: '2020-01-01',
      sold_on: '2024-08-01',
    },
  ],
  disclaimer: 'Not tax advice.',
}
const WORKSHEET = {
  fy: '2024-25',
  row_count: 1,
  columns: ['ISIN Code(2)'],
  rows: [{}],
  title: 't',
  is_draft: true,
  disclaimer: 'd',
}
const INTEGRITY = [
  {
    security: { id: 2, name: 'B', isin: 'Y', symbol: '', security_type: 'mf' },
    folio: { id: 2, number: 'F2', broker: '', folio_type: 'mf' },
    status: 'snapshot_only',
    tax_safe: false,
    units_from_holdings: '5',
    units_from_transactions: null,
    issues: [],
    last_reconciled_at: null,
  },
]

function routeGet(path: string) {
  if (path.endsWith('/capital-gains')) return Promise.resolve({ data: CG })
  if (path.endsWith('/integrity')) return Promise.resolve({ data: INTEGRITY })
  return Promise.resolve({ data: null })
}
const flush = () => new Promise((r) => setTimeout(r, 0))

describe('useCapitalGains', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    get.mockReset()
    post.mockReset()
    get.mockImplementation((path: string) => routeGet(path))
    post.mockResolvedValue({ data: WORKSHEET })
  })

  it('defaults FY and lists options newest-first', () => {
    const { fy, fyOptions } = useCapitalGains(ref(1))
    expect(fy.value).toMatch(/^\d{4}-\d{2}$/)
    expect(fyOptions[0] >= fyOptions[fyOptions.length - 1]).toBe(true)
  })

  it('builds realised gains (GET) and the 112A worksheet (POST) for the FY', async () => {
    const x = useCapitalGains(ref(1))
    x.fy.value = '2024-25'
    x.includeUnreconciled.value = true
    await x.build()

    expect(get).toHaveBeenCalledWith(
      '/api/investors/{investor_id}/exports/capital-gains',
      expect.objectContaining({
        params: { path: { investor_id: 1 }, query: { fy: '2024-25', include_unreconciled: true } },
      }),
    )
    expect(post).toHaveBeenCalledWith(
      '/api/investors/{investor_id}/exports/schedule-112a',
      expect.objectContaining({ body: { fy: '2024-25', include_unreconciled: true } }),
    )
    expect(x.gains.value?.ltcg_total).toBe('1000.00')
    expect(x.worksheetRowCount.value).toBe(1)
    expect(x.built.value).toBe(true)
  })

  it('derives the excluded (non-tax-ready) holdings from the integrity store', async () => {
    const x = useCapitalGains(ref(1))
    await x.build()
    await flush()
    expect(x.excluded.value).toHaveLength(1)
    expect(x.excluded.value[0].status).toBe('snapshot_only')
  })

  it('fails soft when the gains request errors', async () => {
    get.mockImplementation((path: string) =>
      path.endsWith('/capital-gains')
        ? Promise.resolve({ error: { detail: 'bad fy' } })
        : routeGet(path),
    )
    const x = useCapitalGains(ref(1))
    await x.build()
    expect(x.gains.value).toBeNull()
    expect(x.error.value).not.toBeNull()
  })
})
