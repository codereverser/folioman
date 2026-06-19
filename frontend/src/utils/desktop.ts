/** Desktop-shell (PyWebView) integration.
 *
 * When the SPA runs inside the desktop binary, PyWebView injects
 * `window.pywebview.api` — the Python bridge in `webview_api.py`. In a browser it's
 * absent, so every helper degrades to the normal web behaviour. The native file
 * picker hands back the file's bytes (base64), which we rebuild into a `File` so the
 * existing multipart upload path is reused unchanged. */

interface PyWebviewApi {
  pick_cas_file: () => Promise<{ name: string; data: string } | null>
  pick_tradebook_file?: () => Promise<{ name: string; data: string } | null>
  pick_tradebook_files?: () => Promise<{ name: string; data: string }[] | null>
}

interface PyWebview {
  api: PyWebviewApi
}

function bridge(): PyWebview | undefined {
  return (window as unknown as { pywebview?: PyWebview }).pywebview
}

/** True only inside the desktop shell with its API ready. PyWebView injects the
 * bridge slightly after page load, so this is checked lazily (at click time), not
 * cached at module init. */
export function isDesktopShell(): boolean {
  return typeof bridge()?.api?.pick_cas_file === 'function'
}

const MIME_BY_EXT: Record<string, string> = {
  pdf: 'application/pdf',
  csv: 'text/csv',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  xls: 'application/vnd.ms-excel',
}

function base64ToFile(name: string, b64: string): File {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  return new File([bytes], name, { type: MIME_BY_EXT[ext] ?? 'application/octet-stream' })
}

/** Open the native OS file dialog and return the chosen CAS as a `File`, or `null`
 * if the user cancelled (or we're not in the desktop shell). */
export async function pickCasFile(): Promise<File | null> {
  const api = bridge()?.api
  if (!api?.pick_cas_file) return null
  const picked = await api.pick_cas_file()
  if (!picked) return null
  return base64ToFile(picked.name, picked.data)
}

/** Open the native OS file dialog for a broker tradebook (CSV/XLSX) as a `File`,
 * or `null` if cancelled / not in the desktop shell. */
export async function pickTradebookFile(): Promise<File | null> {
  const api = bridge()?.api
  if (!api?.pick_tradebook_file) return null
  const picked = await api.pick_tradebook_file()
  if (!picked) return null
  return base64ToFile(picked.name, picked.data)
}

/** Open the native dialog allowing several broker tradebooks at once (Zerodha
 * exports one per year). Returns the chosen files (empty if cancelled / not
 * desktop). Falls back to the single picker on an older bridge. */
export async function pickTradebookFiles(): Promise<File[]> {
  const api = bridge()?.api
  if (api?.pick_tradebook_files) {
    const picked = (await api.pick_tradebook_files()) ?? []
    return picked.map((p) => base64ToFile(p.name, p.data))
  }
  const one = await pickTradebookFile()
  return one ? [one] : []
}
