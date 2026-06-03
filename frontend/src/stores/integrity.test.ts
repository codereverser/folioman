import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/client', async (importActual) => {
  const actual = await importActual<typeof import('@/api/client')>()
  return { ...actual, api: { GET: vi.fn() } }
})

import { api } from '@/api/client'
import { useIntegrityStore } from './integrity'

const mockGet = vi.mocked(api.GET)

function statusRow(overrides: Record<string, unknown> = {}) {
  return {
    security: { id: 1, name: 'Acme Flexi Cap', isin: 'INF000A01234' },
    status: 'reconciled',
    tax_safe: true,
    units_from_holdings: '100',
    units_from_transactions: '100',
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
  })

  it('fails soft when the backend is unreachable', async () => {
    mockGet.mockResolvedValue({ error: { detail: 'boom' } } as never)
    const store = useIntegrityStore()
    await store.load(10)

    expect(store.rowsFor(10)).toEqual([])
    expect(store.error).not.toBeNull()
  })
})
