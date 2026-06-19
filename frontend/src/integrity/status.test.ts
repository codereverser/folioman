import { describe, expect, it } from 'vitest'
import {
  corporateActionManualNote,
  corporateActionSuggestions,
  corporateActionSuggestionSummary,
  hasCorporateActionSuggestion,
  openingLotIssue,
  openingLotSummary,
  hasIncompleteHistory,
  incompleteHistoryFix,
  incompleteHistoryReason,
  rollupIntegrity,
  rowNeedsAttention,
  toIntegrityStatus,
} from './status'

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

describe('rowNeedsAttention', () => {
  it('includes mismatches and opening-lot snapshots', () => {
    expect(rowNeedsAttention('mismatch', [])).toBe(true)
    expect(
      rowNeedsAttention('snapshot_only', [{ type: 'opening_lot_needed', holding_units: '10' }]),
    ).toBe(true)
    expect(rowNeedsAttention('snapshot_only', [])).toBe(false)
    expect(rowNeedsAttention('user_acknowledged', [])).toBe(false)
  })

  it('includes full_history merger review rows', () => {
    expect(
      rowNeedsAttention('full_history', [
        { type: 'corporate_action_manual', reason: 'ledger_position_not_in_holdings' },
      ]),
    ).toBe(true)
  })
})

describe('incomplete history issues', () => {
  const issues = [{ type: 'incomplete_history', reason: 'orphan_sell' }]

  it('detects orphan-sell folios', () => {
    expect(hasIncompleteHistory(issues)).toBe(true)
    expect(hasIncompleteHistory([])).toBe(false)
  })

  it('returns user-facing reason and fix copy', () => {
    expect(incompleteHistoryReason(issues)).toMatch(/earlier tradebook/)
    expect(incompleteHistoryFix()).toMatch(/earlier-period tradebook/)
  })
})

describe('corporate action issues', () => {
  const suggestion = {
    type: 'corporate_action_suggestion',
    confidence: 'high',
    reference_ids: [42],
    events: [
      {
        action_type: 'bonus',
        subject: 'Bonus 3:1',
        ex_date: '2024-06-15',
        unit_multiplier: '4',
        reference_id: 42,
      },
    ],
  }

  it('parses high-confidence suggestions', () => {
    expect(hasCorporateActionSuggestion([suggestion])).toBe(true)
    const parsed = corporateActionSuggestions([suggestion])
    expect(parsed).toHaveLength(1)
    expect(parsed[0].referenceIds).toEqual([42])
    expect(corporateActionSuggestionSummary(parsed[0])).toMatch(/Bonus 3:1/)
  })

  it('parses multi-event suggestions', () => {
    const multi = {
      type: 'corporate_action_suggestion',
      confidence: 'high',
      reference_ids: [7, 8],
      events: [
        { action_type: 'bonus', subject: 'Bonus 1:1', ex_date: '2018-09-05', unit_multiplier: '2' },
        { action_type: 'split', subject: 'Split 1:2', ex_date: '2020-03-01', unit_multiplier: '2' },
      ],
    }
    const parsed = corporateActionSuggestions([multi])
    expect(parsed[0].referenceIds).toEqual([7, 8])
    expect(corporateActionSuggestionSummary(parsed[0])).toMatch(/Bonus 1:1.*Split 1:2/)
  })

  it('maps replay-mismatch reason to copy', () => {
    expect(
      corporateActionManualNote([{ type: 'corporate_action_manual', reason: 'replay_mismatch' }]),
    ).toMatch(/don’t reconcile/)
  })

  it('maps manual review reasons to copy', () => {
    expect(
      corporateActionManualNote([
        { type: 'corporate_action_manual', reason: 'incomplete_history' },
      ]),
    ).toMatch(/incomplete/)
    expect(corporateActionManualNote([])).toBeNull()
  })

  it('parses opening lot issues', () => {
    const issue = openingLotIssue([{ type: 'opening_lot_needed', holding_units: '50' }])
    expect(issue?.holdingUnits).toBe('50')
    expect(openingLotSummary(issue!)).toMatch(/50 units/)
  })
})
