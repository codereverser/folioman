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
export type CasPreviewOut = Schemas['CasPreviewOut']

/**
 * Typed HTTP client keyed by the OpenAPI paths (regenerate types with
 * `pnpm gen:api`). `baseUrl` is the API origin — the spec's paths already carry
 * the `/api` prefix, so it stays empty for the same-origin production build and
 * points at the local Django server in dev via `VITE_API_BASE`.
 */
export const api = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_BASE ?? '',
})

// The JWT auth interceptor is registered from `api/authInterceptor.ts` (imported
// once in main.ts), kept separate so it can statically import the auth store +
// router without the client↔auth module cycle (the auth store imports this client).

/**
 * Narrow an openapi-fetch `{ data, error }` result to its payload, throwing a
 * labelled error otherwise. For bodyless responses (e.g. DELETE) check `error`
 * directly instead.
 */
export function unwrap<T>(res: { data?: T; error?: unknown }, message: string): T {
  if (res.error !== undefined || res.data === undefined) {
    let detail = ''
    if (res.error && typeof res.error === 'object' && 'detail' in res.error) {
      const d = (res.error as any).detail
      if (typeof d === 'string') detail = `: ${d}`
    }
    throw new Error(message + detail)
  }
  return res.data
}

/** Smoke helper proving the typed client wires through end to end. */
export async function listInvestors(): Promise<InvestorOut[]> {
  return unwrap(await api.GET('/api/investors/'), 'Failed to load investors')
}

/** Build the multipart body for a CAS upload (the browser sets the boundary).
 * openapi-fetch JSON-serializes by default, so we hand it the fields and build
 * the `FormData` ourselves. `includeConfirm` is false for preview (which has no
 * confirm field). */
function casFormData(includeConfirm: boolean) {
  return (body: { file: unknown; password?: string; confirm?: boolean }) => {
    const fd = new FormData()
    fd.append('file', body.file as Blob)
    if (body.password) fd.append('password', body.password)
    if (includeConfirm) fd.append('confirm', String(body.confirm))
    return fd
  }
}

/**
 * Preview a CAS upload: parse it server-side and report whose statement it is
 * (name + masked PAN) and whether the PAN matches an existing investor — without
 * persisting anything. Throws on a wrong password (400) or a PAN-less /
 * unparseable statement (422); the thrown message is safe to surface.
 */
export async function previewCas(file: File, password = ''): Promise<CasPreviewOut> {
  const res = await api.POST('/api/imports/cas/preview', {
    body: { file: file as unknown as string, password },
    bodySerializer: casFormData(false),
  })
  return unwrap(res, 'Could not read this statement')
}

/**
 * Import a CAS PDF (CAMS/KFin MF CAS or NSDL/CDSL eCAS — the server auto-detects
 * and resolves/creates the investor by PAN). Returns the job at HTTP 201; inspect
 * `status`/`result` for the outcome (`success`, `completed_with_warnings`,
 * `needs_confirmation`, or `failed`). Pass `confirm` to apply a destructive eCAS
 * that removes holdings.
 */
export async function importCas(
  file: File,
  password = '',
  confirm = false,
): Promise<ImportJobOut> {
  const res = await api.POST('/api/imports/cas', {
    body: { file: file as unknown as string, password, confirm },
    bodySerializer: casFormData(true),
  })
  return unwrap(res, 'Import failed')
}
