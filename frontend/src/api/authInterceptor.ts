/**
 * JWT auth interceptor (server mode) — registered once via a side-effect import
 * in main.ts.
 *
 * Attaches a bearer token to every API request and routes to /login on a 401. In
 * desktop/local mode the auth store holds no tokens, so no header is attached and
 * nothing 401s — this is inert.
 *
 * It lives here, separate from `client.ts`, so it can **statically** import the
 * auth store and the router: `client.ts` is imported by the auth store, so having
 * the client import the store back would be a module cycle (and forced the old
 * lazy `import()` that Vite warned about). This module is a leaf consumer — nothing
 * imports it — so the static imports are cycle-free.
 */
import type { Middleware } from 'openapi-fetch'
import { useAuthStore } from '@/stores/auth'
import { redirectToLogin } from '@/router'
import { api } from './client'

const AUTH_PREFIX = '/api/auth/'

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    // Never attach a token to (or trigger a refresh for) the token endpoints
    // themselves — that would loop refresh → request → refresh.
    if (request.url.includes(AUTH_PREFIX)) return undefined
    const token = await useAuthStore().validAccessToken()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    return request
  },
  async onResponse({ request, response }) {
    if (response.status === 401 && !request.url.includes(AUTH_PREFIX)) {
      useAuthStore().clear()
      redirectToLogin()
    }
    return response
  },
}

api.use(authMiddleware)
