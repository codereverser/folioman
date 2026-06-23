import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref } from 'vue'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/api/client', () => ({ api: { GET: get } }))

import { useScheme } from './useScheme'

const DETAIL = {
  security: {
    id: 1,
    name: 'Acme Flexi Cap',
    isin: 'INF0001',
    symbol: '',
    security_type: 'mf',
    amfi_code: '120503',
    amc: 'Acme MF',
    category: 'Flexi Cap',
  },
  as_of: '2025-06-01',
  units: '100',
  value_inr: '2000',
  invested_inr: '1000',
  return_pct: 1.0,
  xirr: 0.42,
  day_change_inr: '200',
  day_change_pct: 0.111,
  latest_nav: '20',
  latest_nav_date: '2025-06-01',
  has_transactions: true,
  integrity: [
    {
      security: { id: 1, name: 'Acme Flexi Cap', isin: 'INF0001', symbol: '', security_type: 'mf' },
      folio: { id: 1, number: 'F1', broker: '', folio_type: 'mf' },
      status: 'snapshot_only',
      tax_safe: false,
      units_from_transactions: null,
      units_from_holdings: '100',
      issues: [],
      last_reconciled_at: null,
    },
    {
      security: { id: 1, name: 'Acme Flexi Cap', isin: 'INF0001', symbol: '', security_type: 'mf' },
      folio: { id: 2, number: 'F2', broker: '', folio_type: 'mf' },
      status: 'full_history',
      tax_safe: true,
      units_from_transactions: '100',
      units_from_holdings: null,
      issues: [],
      last_reconciled_at: null,
    },
  ],
  folios: [],
  nav_history: [
    { date: '2025-05-30', nav: '18' },
    { date: '2025-06-01', nav: '20' },
  ],
  dividends: [],
  dividends_received_inr: null,
  dividend_yield_on_cost: null,
  transactions: [],
}

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('useScheme', () => {
  beforeEach(() => get.mockReset())

  it('loads the detail and maps the NAV series for the chart', async () => {
    get.mockResolvedValue({ data: DETAIL })
    const { detail, navSeries, notFound } = useScheme(ref(1), ref(1))
    await flush()

    expect(notFound.value).toBe(false)
    expect(detail.value?.security.name).toBe('Acme Flexi Cap')
    expect(navSeries.value).toEqual([
      { date: '2025-05-30', nav: 18 },
      { date: '2025-06-01', nav: 20 },
    ])
  })

  it('surfaces the worst integrity status across folios', async () => {
    get.mockResolvedValue({ data: DETAIL })
    const { integrityStatus } = useScheme(ref(1), ref(1))
    await flush()

    // snapshot_only (folio F1) is worse than full_history (folio F2).
    expect(integrityStatus.value).toBe('snapshot_only')
  })

  it('flags notFound on a 404 (stale link to a sold scheme)', async () => {
    get.mockResolvedValue({ error: { detail: 'no holding' } })
    const { detail, notFound } = useScheme(ref(1), ref(999))
    await flush()

    expect(notFound.value).toBe(true)
    expect(detail.value).toBeNull()
  })
})
