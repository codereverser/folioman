import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/client', async (importActual) => {
  const actual = await importActual<typeof import('@/api/client')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})

import { api } from '@/api/client'
import { useIntegrityStore } from './integrity'

const mockGet = vi.mocked(api.GET)
const mockPost = vi.mocked(api.POST)

function statusRow(overrides: Record<string, unknown> = {}) {
  return {
    security: {
      id: 1,
      name: 'Acme Flexi Cap',
      isin: 'INF000A01234',
      symbol: '',
      security_type: 'mf',
    },
    folio: { id: 7, number: 'F-001', broker: '', folio_type: 'mf' },
    status: 'reconciled',
    tax_safe: true,
    units_from_holdings: '100',
    units_from_transactions: '100',
    issues: [],
    last_reconciled_at: '2025-06-01T00:00:00Z',
    ...overrides,
  }
}

describe('integrity store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
  })

  it('loads and caches statuses per investor, mapped to rows', async () => {
    mockGet.mockResolvedValue({ data: [statusRow()] } as never)
    const store = useIntegrityStore()
    await store.load(10)

    expect(store.rowsFor(10)).toHaveLength(1)
    expect(store.rowsFor(10)[0].securityId).toBe(1)
    expect(store.rowsFor(10)[0].folioId).toBe(7)
    expect(store.rowsFor(10)[0].folioNumber).toBe('F-001')
    expect(store.rowsFor(10)[0].taxSafe).toBe(true)
    // unknown investor → empty, never undefined
    expect(store.rowsFor(99)).toEqual([])
  })

  it('does not refetch a cached investor unless forced', async () => {
    mockGet.mockResolvedValue({ data: [statusRow()] } as never)
    const store = useIntegrityStore()
    await store.load(10)
    await store.load(10)
    expect(mockGet).toHaveBeenCalledTimes(1)

    await store.load(10, { force: true })
    expect(mockGet).toHaveBeenCalledTimes(2)
  })

  it('rolls statuses up for the dashboard health card', async () => {
    mockGet.mockResolvedValue({ data: [statusRow(), statusRow({ status: 'mismatch' })] } as never)
    const store = useIntegrityStore()
    await store.load(10)

    const rollup = store.rollupFor(10)
    expect(rollup.total).toBe(2)
    expect(rollup.mismatch).toBe(1)
    expect(rollup.needsAttention).toBe(1)
  })

  it('acknowledge updates the matching row in place to user_acknowledged', async () => {
    mockGet.mockResolvedValue({
      data: [
        statusRow({
          security: { id: 1, name: 'A', isin: 'X', symbol: '', security_type: 'mf' },
          folio: { id: 7, number: 'F-001', broker: '', folio_type: 'mf' },
          status: 'mismatch',
          tax_safe: false,
        }),
      ],
    } as never)
    mockPost.mockResolvedValue({
      data: statusRow({ status: 'user_acknowledged', tax_safe: false }),
    } as never)

    const store = useIntegrityStore()
    await store.load(10)
    expect(store.rowsFor(10)[0].status).toBe('mismatch')

    const ok = await store.acknowledge(10, 1, 7)
    expect(ok).toBe(true)
    expect(store.rowsFor(10)[0].status).toBe('user_acknowledged')
    // rollup reflects the change without a refetch
    expect(store.rollupFor(10).acknowledged).toBe(1)
    expect(store.rollupFor(10).needsAttention).toBe(0)
  })

  it('recompute replaces the cached rows', async () => {
    mockGet.mockResolvedValue({
      data: [statusRow({ status: 'mismatch', tax_safe: false })],
    } as never)
    mockPost.mockResolvedValue({
      data: [statusRow({ status: 'reconciled', tax_safe: true })],
    } as never)

    const store = useIntegrityStore()
    await store.load(10)
    expect(store.rowsFor(10)[0].status).toBe('mismatch')

    await store.recompute(10)
    expect(store.rowsFor(10)[0].status).toBe('reconciled')
  })

  it('fails soft when the backend is unreachable', async () => {
    mockGet.mockResolvedValue({ error: { detail: 'boom' } } as never)
    const store = useIntegrityStore()
    await store.load(10)

    expect(store.rowsFor(10)).toEqual([])
    expect(store.error).not.toBeNull()
  })

  it('applyCorporateAction patches the row from the response integrity payload', async () => {
    mockGet.mockResolvedValue({
      data: [
        statusRow({
          status: 'mismatch',
          tax_safe: false,
          issues: [
            {
              type: 'corporate_action_suggestion',
              reference_id: 99,
              subject: 'Bonus 3:1',
              ex_date: '2024-06-15',
              unit_multiplier: '4',
              action_type: 'bonus',
            },
          ],
        }),
      ],
    } as never)
    mockPost.mockResolvedValue({
      data: {
        updated: 0,
        created: 1,
        events_applied: 1,
        integrity: statusRow({ status: 'reconciled', tax_safe: true, issues: [] }),
      },
    } as never)

    const store = useIntegrityStore()
    await store.load(10)
    const ok = await store.applyCorporateAction(10, 1, 7, 99)
    expect(ok).toBe(true)
    expect(store.rowsFor(10)[0].status).toBe('reconciled')
    expect(store.rowsFor(10)[0].issues).toEqual([])
  })
})
