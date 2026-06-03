import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/api/client', () => ({ api: { GET: get, POST: post } }))

import { useTaxExport } from './useTaxExport'

const REPORT = {
  fy: '2024-25',
  include_unreconciled: false,
  row_count: 1,
  columns: ['ISIN Code(2)', 'Balance(14) = 6 - 13'],
  rows: [{ 'ISIN Code(2)': 'INF0001', 'Balance(14) = 6 - 13': '1200' }],
  title: 'Capital-gains worksheet (for review)',
  is_draft: true,
  disclaimer: 'Not tax advice.',
}
const INTEGRITY = [
  { security: { id: 1, name: 'A', isin: 'X', symbol: '', security_type: 'mf' }, folio: { id: 1, number: 'F1', broker: '', folio_type: 'mf' }, status: 'full_history', tax_safe: true, units_from_holdings: null, units_from_transactions: '10', issues: [], last_reconciled_at: null },
  { security: { id: 2, name: 'B', isin: 'Y', symbol: '', security_type: 'mf' }, folio: { id: 2, number: 'F2', broker: '', folio_type: 'mf' }, status: 'snapshot_only', tax_safe: false, units_from_holdings: '5', units_from_transactions: null, issues: [], last_reconciled_at: null },
]
const flush = () => new Promise((r) => setTimeout(r, 0))

describe('useTaxExport', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    get.mockReset()
    post.mockReset()
    get.mockResolvedValue({ data: INTEGRITY })
    post.mockResolvedValue({ data: REPORT })
  })

  it('defaults FY and lists options newest-first', () => {
    const { fy, fyOptions } = useTaxExport(ref(1))
    expect(fy.value).toMatch(/^\d{4}-\d{2}$/)
    expect(fyOptions[0] >= fyOptions[fyOptions.length - 1]).toBe(true)
  })

  it('builds the worksheet and posts the FY + include flag', async () => {
    const x = useTaxExport(ref(1))
    x.fy.value = '2024-25'
    x.includeUnreconciled.value = true
    await x.build()

    expect(post).toHaveBeenCalledWith(
      '/api/investors/{investor_id}/exports/schedule-112a',
      expect.objectContaining({
        params: { path: { investor_id: 1 } },
        body: { fy: '2024-25', include_unreconciled: true },
      }),
    )
    expect(x.report.value?.row_count).toBe(1)
    expect(x.built.value).toBe(true)
  })

  it('derives the excluded (non-tax-ready) holdings from the integrity store', async () => {
    const x = useTaxExport(ref(1))
    await x.build()
    await flush()

    expect(x.excluded.value).toHaveLength(1)
    expect(x.excluded.value[0].status).toBe('snapshot_only')
  })

  it('fails soft when the build errors', async () => {
    post.mockResolvedValue({ error: { detail: 'bad fy' } })
    const x = useTaxExport(ref(1))
    await x.build()

    expect(x.report.value).toBeNull()
    expect(x.error.value).not.toBeNull()
  })
})
