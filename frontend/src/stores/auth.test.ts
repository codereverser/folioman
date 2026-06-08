import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/client', async (importActual) => {
  const actual = await importActual<typeof import('@/api/client')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), use: vi.fn() } }
})

import { api } from '@/api/client'
import { useAuthStore } from './auth'

const mockPost = vi.mocked(api.POST)

/** A JWT whose payload carries `exp` this many seconds from now (sig is dummy —
 * the store only reads `exp`, never verifies). */
function makeJwt(expSecondsFromNow: number): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + expSecondsFromNow }))
  return `${header}.${payload}.sig`
}

describe('auth store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('starts unauthenticated with no tokens (desktop/local mode)', () => {
    const auth = useAuthStore()
    expect(auth.isAuthenticated).toBe(false)
  })

  it('login stores the access token in memory and persists the refresh token', async () => {
    mockPost.mockResolvedValue({ data: { access: makeJwt(1800), refresh: 'refresh-1' } } as never)
    const auth = useAuthStore()

    await auth.login('admin', 'pw')

    expect(auth.accessToken).toMatch(/^/)
    expect(auth.refreshToken).toBe('refresh-1')
    expect(auth.isAuthenticated).toBe(true)
    // refresh token survives a reload; the access token does not.
    expect(localStorage.getItem('folioman.auth.refresh')).toBe('refresh-1')
    const [path] = mockPost.mock.calls[0] as [string]
    expect(path).toBe('/api/auth/token/pair')
  })

  it('login throws a friendly error on bad credentials', async () => {
    mockPost.mockResolvedValue({ error: { detail: 'Invalid credentials.' } } as never)
    const auth = useAuthStore()
    await expect(auth.login('admin', 'wrong')).rejects.toThrow('Invalid username or password.')
    expect(auth.isAuthenticated).toBe(false)
  })

  it('validAccessToken returns the current token without refreshing when it is fresh', async () => {
    mockPost.mockResolvedValue({ data: { access: makeJwt(1800), refresh: 'r' } } as never)
    const auth = useAuthStore()
    await auth.login('admin', 'pw')
    mockPost.mockClear()

    const token = await auth.validAccessToken()

    expect(token).toBe(auth.accessToken)
    expect(mockPost).not.toHaveBeenCalled() // no refresh call for a fresh token
  })

  it('validAccessToken refreshes when the access token is expired', async () => {
    // Restore a session from a persisted refresh token (access starts empty).
    localStorage.setItem('folioman.auth.refresh', 'refresh-1')
    const auth = useAuthStore()
    const fresh = makeJwt(1800)
    mockPost.mockResolvedValue({ data: { access: fresh } } as never)

    const token = await auth.validAccessToken()

    expect(token).toBe(fresh)
    expect(auth.accessToken).toBe(fresh)
    const [path, init] = mockPost.mock.calls[0] as [string, { body: { refresh: string } }]
    expect(path).toBe('/api/auth/token/refresh')
    expect(init.body.refresh).toBe('refresh-1')
  })

  it('clears tokens when the refresh is rejected', async () => {
    localStorage.setItem('folioman.auth.refresh', 'stale')
    const auth = useAuthStore()
    mockPost.mockResolvedValue({ error: { detail: 'token invalid' } } as never)

    const token = await auth.validAccessToken()

    expect(token).toBeNull()
    expect(auth.isAuthenticated).toBe(false)
    expect(localStorage.getItem('folioman.auth.refresh')).toBeNull()
  })

  it('dedupes concurrent refreshes into a single request', async () => {
    localStorage.setItem('folioman.auth.refresh', 'refresh-1')
    const auth = useAuthStore()
    mockPost.mockResolvedValue({ data: { access: makeJwt(1800) } } as never)

    await Promise.all([auth.validAccessToken(), auth.validAccessToken(), auth.validAccessToken()])

    expect(mockPost).toHaveBeenCalledTimes(1)
  })

  it('logout clears both tokens', async () => {
    mockPost.mockResolvedValue({ data: { access: makeJwt(1800), refresh: 'r' } } as never)
    const auth = useAuthStore()
    await auth.login('admin', 'pw')

    auth.logout()

    expect(auth.isAuthenticated).toBe(false)
    expect(localStorage.getItem('folioman.auth.refresh')).toBeNull()
  })

  it('restores an authenticated session from a persisted refresh token', () => {
    localStorage.setItem('folioman.auth.refresh', 'refresh-1')
    const auth = useAuthStore()
    expect(auth.isAuthenticated).toBe(true)
  })
})
