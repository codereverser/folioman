/** Desktop-shell (PyWebView) integration.
 *
 * When the SPA runs inside the desktop binary, PyWebView injects
 * `window.pywebview.api` — the Python bridge in `webview_api.py`. In a browser it's
 * absent, so every helper degrades to the normal web behaviour. The native file
 * picker hands back the file's bytes (base64), which we rebuild into a `File` so the
 * existing multipart upload path is reused unchanged. */

interface PyWebviewApi {
  pick_cas_file: () => Promise<{ name: string; data: string } | null>
  save_csv_file: (filename: string, content: string) => Promise<boolean>
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

function base64ToFile(name: string, b64: string): File {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return new File([bytes], name, { type: 'application/pdf' })
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

/** Open the native OS file save dialog and save `content` to `filename`.
 * Returns true if saved, false if cancelled (or not in desktop shell). */
export async function saveCsvFile(filename: string, content: string): Promise<boolean> {
  const api = bridge()?.api
  if (!api?.save_csv_file) return false
  return await api.save_csv_file(filename, content)
}
