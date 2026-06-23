import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/client', async (importActual) => {
  const actual = await importActual<typeof import('@/api/client')>()
  return { ...actual, api: { GET: vi.fn() } }
})

import { api } from '@/api/client'
import { useMetaStore } from './meta'

const mockGet = vi.mocked(api.GET)

function metaReturns(readOnly: boolean) {
  mockGet.mockResolvedValue({
    data: {
      version: '1.0.0',
      storage: 'server',
      data_location: '',
      key_location: '',
      read_only: readOnly,
    },
  } as never)
}

describe('meta store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
  })

  it('defaults to not read-only until loaded', () => {
    const meta = useMetaStore()
    expect(meta.readOnly).toBe(false)
    expect(meta.loaded).toBe(false)
  })

  it('reflects the read_only flag from /api/meta', async () => {
    metaReturns(true)
    const meta = useMetaStore()
    await meta.ensureLoaded()
    expect(meta.readOnly).toBe(true)
    expect(meta.loaded).toBe(true)
  })

  it('only fetches once', async () => {
    metaReturns(false)
    const meta = useMetaStore()
    await meta.ensureLoaded()
    await meta.ensureLoaded()
    expect(mockGet).toHaveBeenCalledTimes(1)
  })
})
