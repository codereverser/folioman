import { describe, it, expect, beforeEach, vi } from 'vitest'

vi.mock('@/api/client', () => ({ api: { GET: vi.fn() } }))

import { api } from '@/api/client'
import { useRosterMetrics } from './useRosterMetrics'

const mockGet = vi.mocked(api.GET)

describe('useRosterMetrics', () => {
  beforeEach(() => vi.clearAllMocks())

  it('caches a family aggregate and does not refetch', async () => {
    mockGet.mockResolvedValue({
      data: { total_inr: '7012640', stale_count: 2, investor_count: 3, as_of: '2026-05-31' },
    } as never)
    const m = useRosterMetrics()

    await m.loadFamilyAggregate(1)
    await m.loadFamilyAggregate(1)

    expect(mockGet).toHaveBeenCalledOnce()
    expect(m.familyAggregates.value[1]).toEqual({
      totalInr: '7012640',
      staleCount: 2,
      investorCount: 3,
      asOf: '2026-05-31',
    })
  })

  it('maps an investor summary (value, tax-ready split, last import)', async () => {
    mockGet.mockResolvedValue({
      data: {
        investor_id: 10,
        as_of: '2026-05-31',
        total_inr: '7500',
        holdings_count: 3,
        integrity_unit_count: 4,
        tax_ready_count: 1,
        needs_attention_count: 1,
        snapshot_count: 1,
        stale_count: 0,
        last_import_at: '2026-05-30T10:00:00Z',
      },
    } as never)
    const m = useRosterMetrics()

    await m.loadInvestorSummary(10)

    expect(m.investorSummaries.value[10]).toEqual({
      totalInr: '7500',
      holdingsCount: 3,
      integrityUnitCount: 4,
      taxReadyCount: 1,
      needsAttentionCount: 1,
      snapshotCount: 1,
      lastImportAt: '2026-05-30T10:00:00Z',
    })
  })

  it('fails soft when a request errors', async () => {
    mockGet.mockResolvedValue({ error: { detail: 'boom' } } as never)
    const m = useRosterMetrics()

    await m.loadFamilyAggregate(9)

    expect(m.familyAggregates.value[9]).toBeUndefined()
  })
})
