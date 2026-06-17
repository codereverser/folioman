/**
 * Parse an uploaded CSV or XLSX into headers + row objects, client-side.
 *
 * A broker tradebook arrives as CSV or a spreadsheet; SheetJS reads both, so the
 * wizard never uploads the raw file — only the canonical CSV it derives. The
 * matrix→rows shaping is split out as a pure function so it's unit-tested without
 * a real file.
 */
import * as XLSX from 'xlsx'

export interface ParsedTable {
  headers: string[]
  rows: Record<string, string>[]
}

/** Coerce a SheetJS cell to the string the mapping layer expects. */
export function cellToString(value: unknown): string {
  if (value == null || value === '') return ''
  if (value instanceof Date) {
    const y = value.getFullYear()
    const m = String(value.getMonth() + 1).padStart(2, '0')
    const d = String(value.getDate()).padStart(2, '0')
    return `${y}-${m}-${d}`
  }
  if (typeof value === 'number') {
    return String(value)
  }
  return String(value).trim()
}

/**
 * Shape a sheet matrix (array of rows of cell strings; first row = headers) into
 * `{ headers, rows }`. Blank trailing header columns are dropped; fully-empty
 * rows are skipped. Duplicate headers are disambiguated with a numeric suffix so
 * one isn't silently shadowed.
 */
export function rowsFromMatrix(matrix: unknown[][]): ParsedTable {
  if (matrix.length === 0) return { headers: [], rows: [] }
  const seen = new Map<string, number>()
  const headers = matrix[0]
    .map((h) => cellToString(h))
    .filter((h) => h !== '')
    .map((h) => {
      const n = seen.get(h) ?? 0
      seen.set(h, n + 1)
      return n === 0 ? h : `${h} (${n + 1})`
    })
  const rows: Record<string, string>[] = []
  for (const raw of matrix.slice(1)) {
    const cells = raw.map((c) => cellToString(c))
    if (cells.every((c) => c === '')) continue
    const row: Record<string, string> = {}
    headers.forEach((h, i) => {
      row[h] = cells[i] ?? ''
    })
    rows.push(row)
  }
  return { headers, rows }
}

/** Read a CSV/XLSX File into `{ headers, rows }` using the first sheet. */
export async function parseTabularFile(file: File): Promise<ParsedTable> {
  const buffer = await file.arrayBuffer()
  const workbook = XLSX.read(buffer, { type: 'array', cellDates: true })
  const sheetName = workbook.SheetNames[0]
  if (!sheetName) return { headers: [], rows: [] }
  const matrix = XLSX.utils.sheet_to_json<unknown[]>(workbook.Sheets[sheetName], {
    header: 1,
    raw: true,
    defval: '',
    blankrows: false,
  })
  return rowsFromMatrix(matrix)
}
