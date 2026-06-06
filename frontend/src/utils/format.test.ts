import { describe, expect, it } from 'vitest'
import { formatInr, formatInrCompact, formatMonthYear } from './format'

describe('formatInrCompact', () => {
  it('rounds crores and lakhs to L/Cr', () => {
    expect(formatInrCompact(5_000_000)).toBe('₹50L')
    expect(formatInrCompact(31_300_000)).toBe('₹3.13Cr')
    expect(formatInrCompact(1_250_000)).toBe('₹12.5L')
    expect(formatInrCompact(100_000)).toBe('₹1L')
    expect(formatInrCompact(10_000_000)).toBe('₹1Cr')
  })

  it('falls back to the exact figure below ₹1 lakh', () => {
    expect(formatInrCompact(99_999)).toBe(formatInr(99_999))
    expect(formatInrCompact(0)).toBe(formatInr(0))
  })

  it('keeps the sign for negatives', () => {
    expect(formatInrCompact(-5_000_000)).toBe('−₹50L')
  })

  it('accepts Decimal-as-string input', () => {
    expect(formatInrCompact('31300000')).toBe('₹3.13Cr')
  })
})

describe('formatMonthYear', () => {
  it('renders month + year, no day', () => {
    expect(formatMonthYear('2025-06-15')).toBe('Jun 2025')
  })
  it('is empty for null/invalid', () => {
    expect(formatMonthYear(null)).toBe('')
    expect(formatMonthYear('not-a-date')).toBe('')
  })
})
