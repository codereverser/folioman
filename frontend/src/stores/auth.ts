import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api/client'

const REFRESH_STORAGE_KEY = 'folioman.auth.refresh'
// Refresh slightly before the access token's real expiry to absorb clock skew
// and in-flight latency, so requests rarely race a just-expired token.
const EXP_SKEW_SECONDS = 30

function loadRefreshToken(): string | null {
  if (typeof localStorage === 'undefined') return null
  return localStorage.getItem(REFRESH_STORAGE_KEY)
}

/** The `exp` (seconds since epoch) from a JWT payload, or null if unreadable. */
function jwtExp(token: string): number | null {
  const parts = token.split('.')
  if (parts.length !== 3) return null
  try {
    const json = atob(parts[1].replace(/-/g, '+').replace(/_/g, '/'))
    const payload = JSON.parse(json) as { exp?: number }
    return typeof payload.exp === 'number' ? payload.exp : null
  } catch {
    return null
  }
}

/** True when the token is at/within `skew` seconds of expiring. An unreadable
 * token is treated as usable — the 401 path is the backstop if it's actually bad. */
function isExpired(token: string, skew = EXP_SKEW_SECONDS): boolean {
  const exp = jwtExp(token)
  if (exp === null) return false
  return Date.now() / 1000 >= exp - skew
}

/**
 * JWT auth state for server mode. The access token lives only in memory (gone on
 * reload); the refresh token is persisted so a reload restores the session without
 * a re-login. There is no httpOnly cookie — the API issues bearer tokens
 * (django-ninja-jwt), not cookies; the trade-off is documented at the call site.
 *
 * In desktop / local mode no tokens are ever set: `isAuthenticated` stays false,
 * requests carry no Authorization header (the API ignores it there), nothing ever
 * 401s, and so the login screen is never reached. This store is inert there.
 */
export const useAuthStore = defineStore('auth', () => {
  const accessToken = ref<string | null>(null)
  const refreshToken = ref<string | null>(loadRefreshToken())

  // "Authenticated" = we hold an access token, or a refresh token we can mint one
  // from. A stale refresh token reads as authenticated until its first use fails,
  // which clears it (and the interceptor then routes to login).
  const isAuthenticated = computed(
    () => accessToken.value !== null || refreshToken.value !== null,
  )

  function persistRefresh(token: string | null): void {
    refreshToken.value = token
    if (typeof localStorage === 'undefined') return
    if (token) localStorage.setItem(REFRESH_STORAGE_KEY, token)
    else localStorage.removeItem(REFRESH_STORAGE_KEY)
  }

  function setTokens(access: string, refresh: string): void {
    accessToken.value = access
    persistRefresh(refresh)
  }

  function clear(): void {
    accessToken.value = null
    persistRefresh(null)
  }

  // Dedupe concurrent refreshes: a burst of requests all seeing an expired access
  // token must trigger exactly one /token/refresh, not one each.
  let refreshing: Promise<string | null> | null = null

  async function refreshAccess(): Promise<string | null> {
    if (refreshing) return refreshing
    const token = refreshToken.value
    if (!token) return null
    refreshing = (async () => {
      const res = await api.POST('/api/auth/token/refresh', { body: { refresh: token } })
      if (res.data?.access) {
        accessToken.value = res.data.access
        return res.data.access
      }
      clear() // refresh rejected (expired / invalid) → full logged-out state
      return null
    })()
    try {
      return await refreshing
    } finally {
      refreshing = null
    }
  }

  /** A usable access token for the next request — refreshing first if the current
   * one is missing or about to expire. null when unauthenticated (desktop/local),
   * in which case the caller sends no Authorization header. */
  async function validAccessToken(): Promise<string | null> {
    if (accessToken.value && !isExpired(accessToken.value)) return accessToken.value
    if (refreshToken.value) return refreshAccess()
    return null
  }

  async function login(username: string, password: string): Promise<void> {
    const res = await api.POST('/api/auth/token/pair', { body: { username, password } })
    if (!res.data?.access || !res.data?.refresh) {
      throw new Error('Invalid username or password.')
    }
    setTokens(res.data.access, res.data.refresh)
  }

  function logout(): void {
    clear()
  }

  return {
    accessToken,
    refreshToken,
    isAuthenticated,
    setTokens,
    clear,
    refreshAccess,
    validAccessToken,
    login,
    logout,
  }
})
