import { describe, expect, it } from 'vitest'
import {
  autoDetectMapping,
  buildCanonicalRows,
  isValidDematNumber,
  mappingErrors,
  normalizeDecimalCell,
} from './tradebook'

describe('autoDetectMapping', () => {
  it('maps a Zerodha-style tradebook header onto canonical fields', () => {
    const headers = ['symbol', 'isin', 'trade_date', 'trade_type', 'quantity', 'price', 'trade_id']
    const m = autoDetectMapping(headers)
    expect(m.date).toBe('trade_date')
    expect(m.transaction_type).toBe('trade_type')
    expect(m.units).toBe('quantity')
    expect(m.price).toBe('price')
    expect(m.symbol).toBe('symbol')
    expect(m.isin).toBe('isin')
    expect(m.source_ref).toBe('trade_id')
  })

  it('leaves an unmatched field unmapped', () => {
    const m = autoDetectMapping(['date', 'type', 'qty', 'price'])
    expect(m.brokerage).toBe('')
    expect(m.isin).toBe('')
  })

  it('does not assign one header to two fields', () => {
    // "price" matches `price`; nothing else should grab it.
    const m = autoDetectMapping(['date', 'side', 'shares', 'price'])
    const used = Object.values(m).filter(Boolean)
    expect(new Set(used).size).toBe(used.length)
  })
})

describe('mappingErrors', () => {
  const ok = {
    date: 'trade_date',
    transaction_type: 'trade_type',
    units: 'quantity',
    price: 'price',
    isin: 'isin',
  }

  it('passes a complete mapping', () => {
    expect(mappingErrors(ok)).toEqual([])
  })

  it('flags a missing required field', () => {
    const m = { ...ok, units: '' }
    expect(mappingErrors(m).some((e) => /units/i.test(e))).toBe(true)
  })

  it('flags when no identity column is mapped', () => {
    const m = { date: 'd', transaction_type: 't', units: 'u', price: 'p' }
    expect(mappingErrors(m).some((e) => /symbol|isin|name/i.test(e))).toBe(true)
  })

  it('accepts symbol alone as identity', () => {
    const m = { date: 'd', transaction_type: 't', units: 'u', price: 'p', symbol: 'sym' }
    expect(mappingErrors(m)).toEqual([])
  })
})

describe('isValidDematNumber', () => {
  it('accepts a 16-digit CDSL BO ID', () => {
    expect(isValidDematNumber('1208160000000001')).toBe(true)
  })
  it('accepts an NSDL IN + 14 digit id (case-insensitive)', () => {
    expect(isValidDematNumber('IN30021412345678')).toBe(true)
    expect(isValidDematNumber('in30021412345678')).toBe(true)
  })
  it('rejects wrong length / shape', () => {
    expect(isValidDematNumber('12345')).toBe(false)
    expect(isValidDematNumber('NOTAVALIDID')).toBe(false)
    expect(isValidDematNumber('IN3002141234567')).toBe(false) // 13 digits
  })
})

describe('normalizeDecimalCell', () => {
  it('strips thousands separators and currency glyphs', () => {
    expect(normalizeDecimalCell('2,800.00')).toBe('2800.00')
    expect(normalizeDecimalCell('₹2,800')).toBe('2800')
  })

  it('leaves plain decimals unchanged', () => {
    expect(normalizeDecimalCell('50.000000')).toBe('50.000000')
  })
})

describe('buildCanonicalRows', () => {
  const mapping = {
    date: 'trade_date',
    transaction_type: 'trade_type',
    units: 'quantity',
    price: 'price',
    symbol: 'symbol',
    isin: 'isin',
    source_ref: 'trade_id',
  }
  const opts = { folioNumber: '1208160000000001', broker: 'Zerodha' }

  it('projects file rows onto canonical rows with injected constants', () => {
    const rows = [
      {
        symbol: 'RELIANCE',
        isin: 'INE002A01018',
        trade_date: '2024-01-15',
        trade_type: 'buy',
        quantity: '10',
        price: '2800',
        trade_id: 'T1',
      },
    ]
    const [row] = buildCanonicalRows(rows, mapping, opts)
    expect(row.security_type).toBe('equity')
    expect(row.date).toBe('2024-01-15')
    expect(row.transaction_type).toBe('buy')
    expect(row.units).toBe('10')
    expect(row.price).toBe('2800')
    expect(row.source_ref).toBe('T1')
    expect(row.folio_number).toBe('1208160000000001')
    expect(row.broker).toBe('Zerodha')
  })

  it('falls back to symbol then isin for an unmapped name', () => {
    const rows = [
      {
        symbol: 'RELIANCE',
        isin: 'INE002A01018',
        trade_date: 'd',
        trade_type: 'buy',
        quantity: '1',
        price: '1',
        trade_id: 'x',
      },
      {
        symbol: '',
        isin: 'INE999',
        trade_date: 'd',
        trade_type: 'buy',
        quantity: '1',
        price: '1',
        trade_id: 'y',
      },
    ]
    const built = buildCanonicalRows(rows, mapping, opts)
    expect(built[0].name).toBe('RELIANCE')
    expect(built[1].name).toBe('INE999')
  })

  it('normalizes formatted numeric columns from XLSX display strings', () => {
    const rows = [
      {
        symbol: 'RELIANCE',
        isin: 'INE002A01018',
        trade_date: '2024-01-15',
        trade_type: 'buy',
        quantity: '2,800.00',
        price: '₹2,800',
        trade_id: 'T1',
      },
    ]
    const [row] = buildCanonicalRows(rows, mapping, opts)
    expect(row.units).toBe('2800.00')
    expect(row.price).toBe('2800')
  })

  it('drops a fully-blank trailing row', () => {
    const rows = [
      {
        symbol: 'RELIANCE',
        isin: 'INE002A01018',
        trade_date: 'd',
        trade_type: 'buy',
        quantity: '1',
        price: '1',
        trade_id: 'x',
      },
      {
        symbol: '',
        isin: '',
        trade_date: '',
        trade_type: '',
        quantity: '',
        price: '',
        trade_id: '',
      },
    ]
    expect(buildCanonicalRows(rows, mapping, opts)).toHaveLength(1)
  })
})
