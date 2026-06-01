import createClient from 'openapi-fetch'
import type { paths, components } from './schema'

/** Shorthands for the generated component schemas. */
export type Schemas = components['schemas']
export type InvestorOut = Schemas['InvestorOut']
export type InvestorIn = Schemas['InvestorIn']
export type InvestorUpdate = Schemas['InvestorUpdate']
export type FamilyOut = Schemas['FamilyOut']
export type FamilyIn = Schemas['FamilyIn']
export type FolioOut = Schemas['FolioOut']

/**
 * Typed HTTP client keyed by the OpenAPI paths (regenerate types with
 * `pnpm gen:api`). `baseUrl` is the API origin — the spec's paths already carry
 * the `/api` prefix, so it stays empty for the same-origin production build and
 * points at the local Django server in dev via `VITE_API_BASE`.
 */
export const api = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_BASE ?? '',
})

/**
 * Narrow an openapi-fetch `{ data, error }` result to its payload, throwing a
 * labelled error otherwise. For bodyless responses (e.g. DELETE) check `error`
 * directly instead.
 */
export function unwrap<T>(res: { data?: T; error?: unknown }, message: string): T {
  if (res.error !== undefined || res.data === undefined) {
    throw new Error(message)
  }
  return res.data
}

/** Smoke helper proving the typed client wires through end to end. */
export async function listInvestors(): Promise<InvestorOut[]> {
  return unwrap(await api.GET('/api/investors/'), 'Failed to load investors')
}
