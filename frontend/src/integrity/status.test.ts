import { describe, expect, it } from 'vitest'
import { rollupIntegrity, toIntegrityStatus } from './status'

describe('toIntegrityStatus', () => {
  it('passes through known statuses case-insensitively', () => {
    expect(toIntegrityStatus('RECONCILED')).toBe('reconciled')
    expect(toIntegrityStatus('snapshot_only')).toBe('snapshot_only')
  })

  it('falls back to unknown for unrecognised or empty input', () => {
    expect(toIntegrityStatus('garbage')).toBe('unknown')
    expect(toIntegrityStatus(null)).toBe('unknown')
    expect(toIntegrityStatus(undefined)).toBe('unknown')
  })
})

describe('rollupIntegrity', () => {
  it('counts tax-ready, attention, and per-bucket totals', () => {
    const r = rollupIntegrity([
      'full_history',
      'reconciled',
      'reconciled',
      'snapshot_only',
      'mismatch',
      'mismatch',
      'user_acknowledged',
    ])
    expect(r.total).toBe(7)
    expect(r.verified).toBe(3) // full_history + 2 reconciled
    expect(r.taxReady).toBe(3)
    expect(r.snapshot).toBe(1)
    expect(r.mismatch).toBe(2)
    expect(r.needsAttention).toBe(2)
    expect(r.acknowledged).toBe(1)
  })

  it('handles an empty set', () => {
    expect(rollupIntegrity([])).toMatchObject({ total: 0, taxReady: 0, needsAttention: 0 })
  })
})
