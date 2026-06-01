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
export type ImportJobOut = Schemas['ImportJobOut']

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

/**
 * Upload a CAS PDF (CAMS/KFin MF CAS or NSDL/CDSL eCAS — the server auto-detects).
 * The endpoint always returns the job at HTTP 201; inspect `status`/`result` for
 * the outcome (`success`, `completed_with_warnings`, `needs_confirmation`, or
 * `failed`). Pass `confirm` to apply a destructive eCAS that removes holdings.
 *
 * Multipart upload: openapi-fetch JSON-serializes by default, so we hand it the
 * fields and build the `FormData` ourselves (the browser sets the boundary).
 */
export async function importCas(
  investorId: number,
  file: File,
  password = '',
  confirm = false,
): Promise<ImportJobOut> {
  const res = await api.POST('/api/investors/{investor_id}/imports/cas', {
    params: { path: { investor_id: investorId } },
    body: { file: file as unknown as string, password, confirm },
    bodySerializer(body) {
      const fd = new FormData()
      fd.append('file', body.file as unknown as Blob)
      if (body.password) fd.append('password', body.password)
      fd.append('confirm', String(body.confirm))
      return fd
    },
  })
  return unwrap(res, 'Import failed')
}
