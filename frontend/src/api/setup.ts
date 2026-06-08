import { api } from '@/api/client'

/**
 * First-run setup (server mode only). The server exposes a public, zero-users
 * gated endpoint to create the initial admin; this module fronts it for the
 * router guard and the setup view. In desktop/local mode `needs_admin` is always
 * false, so the setup screen is never reached.
 *
 * `token_required` is true when the server printed a console setup token that
 * must accompany admin creation (LAN hardening — see 9.9).
 */
export interface SetupState {
  needs_admin: boolean
  token_required: boolean
}

// Cached for the session: the answer only flips once (false → setup done), and we
// mark it complete locally right after creating the admin.
let cached: SetupState | null = null
let inflight: Promise<SetupState> | null = null

/** The setup state (cached after the first call). */
export async function fetchSetupState(): Promise<SetupState> {
  if (cached) return cached
  if (inflight) return inflight
  inflight = (async () => {
    try {
      const res = await api.GET('/api/setup/state')
      cached = {
        needs_admin: res.data?.needs_admin ?? false,
        token_required: res.data?.token_required ?? false,
      }
    } catch {
      cached = { needs_admin: false, token_required: false } // never trap the user on setup
    } finally {
      inflight = null
    }
    return cached as SetupState
  })()
  return inflight
}

/** Whether the first-admin setup screen is needed (convenience for the guard). */
export async function fetchSetupNeeded(): Promise<boolean> {
  return (await fetchSetupState()).needs_admin
}

/** Mark setup done so the guard stops routing to /setup after admin creation. */
export function markSetupComplete(): void {
  cached = { needs_admin: false, token_required: false }
}

/** Reset the cache (tests). */
export function _resetSetupCache(): void {
  cached = null
  inflight = null
}

export interface TokenPair {
  access: string
  refresh: string
}

/** Create the first admin; returns a token pair (the caller signs in with it).
 * `token` is the console setup token (empty when the server doesn't require one). */
export async function createFirstAdmin(
  username: string,
  password: string,
  email = '',
  token = '',
): Promise<TokenPair> {
  const res = await api.POST('/api/setup/admin', { body: { username, password, email, token } })
  if (!res.data?.access || !res.data?.refresh) {
    const detail = (res.error as { detail?: string } | undefined)?.detail
    throw new Error(detail || 'Could not complete setup.')
  }
  return res.data
}
