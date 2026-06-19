import { describe, expect, it } from 'vitest'
import { cellToString, headerRowIndex, rowsFromMatrix } from './parseTabular'

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
    const { headers } = rowsFromMatrix([
      ['price', 'price'],
      ['1', '2'],
    ])
    expect(headers).toEqual(['price', 'price (2)'])
  })

  it('returns empty for an empty matrix', () => {
    expect(rowsFromMatrix([])).toEqual({ headers: [], rows: [] })
  })

  it('skips a broker XLSX preamble and finds the real header row', () => {
    // Shape of a Zerodha Console XLSX: title + metadata + blank, then the header.
    const { headers, rows } = rowsFromMatrix([
      ['Tradebook', '', '', ''],
      ['Client ID', 'AB1234', '', ''],
      ['Period', '2023-04-01 to 2024-03-31', '', ''],
      ['', '', '', ''],
      ['symbol', 'isin', 'trade_type', 'quantity'],
      ['RELIANCE', 'INE002A01018', 'buy', '10'],
      ['TCS', 'INE467B01029', 'sell', '5'],
    ])
    expect(headers).toEqual(['symbol', 'isin', 'trade_type', 'quantity'])
    expect(rows).toEqual([
      { symbol: 'RELIANCE', isin: 'INE002A01018', trade_type: 'buy', quantity: '10' },
      { symbol: 'TCS', isin: 'INE467B01029', trade_type: 'sell', quantity: '5' },
    ])
  })
})

describe('headerRowIndex', () => {
  it('is 0 for a clean table', () => {
    expect(headerRowIndex([['a', 'b', 'c'], ['1', '2', '3']])).toBe(0)
  })

  it('skips sparse preamble rows', () => {
    expect(
      headerRowIndex([
        ['Tradebook', '', '', ''],
        ['Client', 'X', '', ''],
        ['', '', '', ''],
        ['symbol', 'isin', 'type', 'qty'],
        ['R', 'I', 'buy', '1'],
      ]),
    ).toBe(3)
  })
})

describe('cellToString', () => {
  it('formats Date cells as ISO dates', () => {
    expect(cellToString(new Date('2024-01-15T12:00:00Z'))).toBe('2024-01-15')
  })

  it('stringifies numeric cells without locale formatting', () => {
    expect(cellToString(2800)).toBe('2800')
    expect(cellToString(50.5)).toBe('50.5')
  })
})
