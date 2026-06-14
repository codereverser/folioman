import { describe, expect, it } from 'vitest'
import { rowsFromMatrix } from './parseTabular'

describe('rowsFromMatrix', () => {
  it('uses the first row as headers and keys each row by them', () => {
    const { headers, rows } = rowsFromMatrix([
      ['symbol', 'qty', 'price'],
      ['RELIANCE', '10', '2800'],
      ['TCS', '5', '3800'],
    ])
    expect(headers).toEqual(['symbol', 'qty', 'price'])
    expect(rows).toEqual([
      { symbol: 'RELIANCE', qty: '10', price: '2800' },
      { symbol: 'TCS', qty: '5', price: '3800' },
    ])
  })

  it('trims cells, drops blank header columns, and skips empty rows', () => {
    const { headers, rows } = rowsFromMatrix([
      [' symbol ', 'qty', ''],
      ['RELIANCE', ' 10 ', ''],
      ['', '', ''],
    ])
    expect(headers).toEqual(['symbol', 'qty'])
    expect(rows).toEqual([{ symbol: 'RELIANCE', qty: '10' }])
  })

  it('disambiguates duplicate headers', () => {
    const { headers } = rowsFromMatrix([['price', 'price'], ['1', '2']])
    expect(headers).toEqual(['price', 'price (2)'])
  })

  it('returns empty for an empty matrix', () => {
    expect(rowsFromMatrix([])).toEqual({ headers: [], rows: [] })
  })
})
