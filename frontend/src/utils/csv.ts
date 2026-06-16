/** RFC-4180 CSV building + a client-side download helper. */

/** Quote a field when it contains a comma, quote, or newline; double inner quotes. */
function escapeField(value: string): string {
  return /[",\n\r]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value
}

/**
 * Build a CSV string from ordered `columns` and `rows` keyed by those columns.
 * A missing cell becomes an empty field. CRLF line endings (Excel-friendly).
 */
export function toCsv(columns: string[], rows: Record<string, string>[]): string {
  const header = columns.map(escapeField).join(',')
  const body = rows.map((row) => columns.map((c) => escapeField(row[c] ?? '')).join(','))
  return [header, ...body].join('\r\n')
}

import { isDesktopShell, saveCsvFile } from './desktop'

/**
 * Trigger a browser download of `text` as a file. No-op without a DOM.
 * If running in the PyWebView desktop shell, it opens a native save dialog.
 * Returns true if the file was saved or download initiated, false if cancelled/failed.
 */
export async function downloadText(filename: string, text: string, type = 'text/csv'): Promise<boolean> {
  if (isDesktopShell()) {
    return await saveCsvFile(filename, text)
  }

  if (typeof document === 'undefined') return false
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
  return true
}
