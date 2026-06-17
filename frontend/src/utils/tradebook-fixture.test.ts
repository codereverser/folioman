import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { toCsv } from './csv'
import { parseTabularFile } from './parseTabular'
import {
  autoDetectMapping,
  buildCanonicalRows,
  CANONICAL_COLUMNS,
  mappingErrors,
} from './tradebook'

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), '../test-fixtures')

function parseCsvText(text: string): { headers: string[]; rows: Record<string, string>[] } {
  const lines = text.trim().split(/\r?\n/)
  const headers = lines[0].split(',')
  const rows = lines.slice(1).map((line) => {
    const cells = line.split(',')
    const row: Record<string, string> = {}
    headers.forEach((h, i) => {
      row[h] = cells[i] ?? ''
    })
    return row
  })
  return { headers, rows }
}

describe('Zerodha tradebook fixtures', () => {
  const account = { folioNumber: '1208160000000001', broker: 'Zerodha' }

  it('maps the 2024 CSV fixture onto canonical rows', () => {
    const text = readFileSync(join(FIXTURES, 'tradebook-zerodha-2024.csv'), 'utf8')
    const { headers, rows } = parseCsvText(text)
    const mapping = autoDetectMapping(headers)
    expect(mappingErrors(mapping)).toEqual([])
    const canonical = buildCanonicalRows(rows, mapping, account)
    expect(canonical).toHaveLength(6)
    const dream = canonical.filter((r) => r.symbol === 'DREAMFOLKS')
    expect(dream).toHaveLength(5)
    expect(new Set(dream.map((r) => r.source_ref)).size).toBe(5)
    const csv = toCsv(CANONICAL_COLUMNS, canonical)
    expect(csv.split('\r\n')).toHaveLength(7) // header + 6 rows
  })

  it('parses a minimal XLSX fixture through the same wizard path', async () => {
    const buffer = readFileSync(join(FIXTURES, 'tradebook-zerodha-minimal.xlsx'))
    const file = new File([buffer], 'tradebook-zerodha-minimal.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
    const { headers, rows } = await parseTabularFile(file)
    expect(headers).toContain('trade_date')
    expect(rows.length).toBeGreaterThanOrEqual(1)
    const mapping = autoDetectMapping(headers)
    expect(mappingErrors(mapping)).toEqual([])
    const canonical = buildCanonicalRows(rows, mapping, account)
    expect(canonical.length).toBe(rows.length)
    expect(canonical[0].symbol).toBeTruthy()
    expect(canonical[0].isin).toMatch(/^INE/)
  })
})
