import { describe, it, expect } from 'vitest'
import { currentFy, fyLabel, fyOptions } from './fy'

describe('fy helpers', () => {
  it('labels a start year as YYYY-YY', () => {
    expect(fyLabel(2024)).toBe('2024-25')
    expect(fyLabel(2018)).toBe('2018-19')
    expect(fyLabel(2009)).toBe('2009-10')
  })

  it('maps Jan–Mar to the prior FY and Apr+ to the current one', () => {
    expect(currentFy(new Date('2026-03-15'))).toBe('2025-26')
    expect(currentFy(new Date('2026-04-01'))).toBe('2026-27')
  })

  it('lists FYs newest-first back to 2018-19 by default', () => {
    const opts = fyOptions(new Date('2026-06-03'))
    expect(opts[0]).toBe('2026-27')
    expect(opts.at(-1)).toBe('2018-19')
    expect(opts).toContain('2025-26')
  })
})
