import { describe, it, expect, beforeEach, vi } from 'vitest'

vi.mock('@/api/client', async (importActual) => {
  const actual = await importActual<typeof import('@/api/client')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), use: vi.fn() } }
})

import { api } from '@/api/client'
import {
  fetchSetupNeeded,
  fetchSetupState,
  markSetupComplete,
  createFirstAdmin,
  _resetSetupCache,
} from './setup'

const mockGet = vi.mocked(api.GET)
const mockPost = vi.mocked(api.POST)

describe('setup api', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    _resetSetupCache()
  })

  it('reports whether a first admin is needed', async () => {
    mockGet.mockResolvedValue({ data: { needs_admin: true, token_required: false } } as never)
    expect(await fetchSetupNeeded()).toBe(true)
  })

  it('reports whether a console token is required', async () => {
    mockGet.mockResolvedValue({ data: { needs_admin: true, token_required: true } } as never)
    expect(await fetchSetupState()).toEqual({ needs_admin: true, token_required: true })
  })

  it('caches the answer after the first call', async () => {
    mockGet.mockResolvedValue({ data: { needs_admin: true } } as never)
    await fetchSetupNeeded()
    await fetchSetupNeeded()
    expect(mockGet).toHaveBeenCalledTimes(1)
  })

  it('treats a failed probe as "no setup needed" (never traps the user)', async () => {
    mockGet.mockRejectedValue(new Error('network'))
    expect(await fetchSetupNeeded()).toBe(false)
  })

  it('markSetupComplete flips the cached answer to false', async () => {
    mockGet.mockResolvedValue({ data: { needs_admin: true } } as never)
    expect(await fetchSetupNeeded()).toBe(true)
    markSetupComplete()
    expect(await fetchSetupNeeded()).toBe(false) // served from cache, no new GET
    expect(mockGet).toHaveBeenCalledTimes(1)
  })

  it('createFirstAdmin returns the token pair and forwards the setup token', async () => {
    mockPost.mockResolvedValue({ data: { access: 'a', refresh: 'r' } } as never)
    const tokens = await createFirstAdmin('boss', 's3cret-pw', '', 'console-tok')
    expect(tokens).toEqual({ access: 'a', refresh: 'r' })
    const [path, init] = mockPost.mock.calls[0] as [string, { body: Record<string, unknown> }]
    expect(path).toBe('/api/setup/admin')
    expect(init.body.token).toBe('console-tok')
  })

  it('createFirstAdmin surfaces the server error message', async () => {
    mockPost.mockResolvedValue({ error: { detail: 'Setup has already been completed.' } } as never)
    await expect(createFirstAdmin('x', 's3cret-pw')).rejects.toThrow('Setup has already been completed.')
  })
})
