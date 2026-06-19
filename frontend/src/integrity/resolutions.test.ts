import { describe, expect, it } from 'vitest'
import {
  applicableResolutions,
  actionableResolutions,
  metaResolutions,
  workResolutions,
} from './resolutions'
import type { IntegrityRow } from '@/stores/integrity'

const base = {
  status: 'mismatch' as const,
  caSyncedAt: '2026-06-01T00:00:00Z',
}

function row(partial: Partial<IntegrityRow> = {}): IntegrityRow {
  return {
    securityId: 1,
    folioId: 1,
    name: 'Test',
    isin: 'INE000',
    securityType: 'equity',
    folioNumber: '1',
    folioType: 'demat',
    broker: '',
    status: 'mismatch',
    taxSafe: false,
    caSyncedAt: '2026-01-01',
    unitsFromHoldings: '10',
    unitsFromTransactions: '8',
    issues: [],
    ledgerThrough: null,
    snapshotAsOf: null,
    lastReconciledAt: null,
    ...partial,
  }
}

describe('applicableResolutions', () => {
  it('suggests apply when a high-confidence CA replay matches', () => {
    const r = applicableResolutions({
      ...base,
      issues: [
        {
          type: 'corporate_action_suggestion',
          reference_ids: [1],
          events: [{ subject: 'Bonus 3:1' }],
        },
      ],
    })
    expect(r).toEqual([
      expect.objectContaining({ id: 'apply_ca_suggestion', label: 'Apply action' }),
    ])
  })

  it('offers nothing on a reconciled row, even with a stale manual flag', () => {
    // A leftover corporate_action_manual after a CA was applied must not surface
    // manual-CA authoring on an already-reconciled holding.
    const r = applicableResolutions({
      status: 'reconciled',
      caSyncedAt: '2026-06-01T00:00:00Z',
      issues: [{ type: 'corporate_action_manual', reason: 'replay_mismatch' }],
    })
    expect(r).toEqual([])
  })

  it('still offers the opening lot when incomplete history co-exists', () => {
    // HDFC Bank: orphan sells in the tradebook (incomplete_history) AND an eCAS-only
    // holding from a merger (opening_lot_needed). The opening lot stays actionable.
    const r = applicableResolutions({
      status: 'snapshot_only',
      caSyncedAt: null,
      issues: [
        { type: 'incomplete_history', reason: 'orphan_sell' },
        { type: 'opening_lot_needed', holding_units: '168' },
      ],
    })
    expect(r.map((x) => x.openingLotClassification)).toEqual([
      'ipo_allotment',
      'transfer_in',
      'demerger_result',
    ])
  })

  it('maps opening_lot_needed to all three opening classifications', () => {
    const r = applicableResolutions({
      status: 'snapshot_only',
      caSyncedAt: null,
      issues: [{ type: 'opening_lot_needed', holding_units: '50' }],
    })
    expect(r.map((x) => x.openingLotClassification)).toEqual([
      'ipo_allotment',
      'transfer_in',
      'demerger_result',
    ])
  })

  it('offers merger + remap on full_history ledger_position rows', () => {
    const r = actionableResolutions({
      status: 'full_history',
      caSyncedAt: '2026-01-01',
      issues: [{ type: 'corporate_action_manual', reason: 'ledger_position_not_in_holdings' }],
    })
    expect(r.map((x) => x.label)).toEqual(['Resolve as merger', 'Remap ISIN (1:1)'])
    expect(r.some((x) => x.id === 'acknowledge')).toBe(false)
  })

  it('maps ledger_position_not_in_holdings to merger and identity remap on mismatch', () => {
    const r = actionableResolutions({
      ...base,
      issues: [{ type: 'corporate_action_manual', reason: 'ledger_position_not_in_holdings' }],
    })
    expect(r.map((x) => x.label)).toEqual(['Resolve as merger', 'Remap ISIN (1:1)', 'Acknowledge'])
    expect(r[0].manualCaKind).toBe('merger')
  })

  it('maps holding_below_ledger to buyback (transfer_out listed but not actionable)', () => {
    const all = applicableResolutions({
      ...base,
      issues: [{ type: 'corporate_action_manual', reason: 'holding_below_ledger' }],
    })
    expect(all.some((x) => x.label === 'Record buyback')).toBe(true)
    expect(all.some((x) => x.label === 'Transfer out' && x.available === false)).toBe(true)

    const actionable = actionableResolutions({
      ...base,
      issues: [{ type: 'corporate_action_manual', reason: 'holding_below_ledger' }],
    })
    expect(actionable.map((x) => x.label)).toEqual(['Record buyback', 'Acknowledge'])
  })

  it('offers fetch when mismatch has no CA sync and no manual hint', () => {
    const r = applicableResolutions({
      status: 'mismatch',
      caSyncedAt: null,
      issues: [],
    })
    expect(r.map((x) => x.id)).toEqual(['fetch_corporate_actions', 'acknowledge'])
  })

  it('offers generic manual CA kinds on bare mismatch after fetch', () => {
    const r = actionableResolutions({
      status: 'mismatch',
      caSyncedAt: '2026-01-01',
      issues: [],
    })
    expect(r.some((x) => x.manualCaKind === 'bonus')).toBe(true)
    expect(r.some((x) => x.manualCaKind === 'buyback')).toBe(true)
    expect(r.some((x) => x.id === 'acknowledge')).toBe(true)
  })

  it('returns nothing actionable for incomplete history', () => {
    expect(
      applicableResolutions({
        ...base,
        issues: [{ type: 'incomplete_history', reason: 'orphan_sell' }],
      }),
    ).toEqual([])
  })

  it('returns un-acknowledge for user_acknowledged status', () => {
    const r = applicableResolutions({
      status: 'user_acknowledged',
      caSyncedAt: null,
      issues: [],
    })
    expect(r).toEqual([expect.objectContaining({ id: 'unacknowledge' })])
  })

  it('lists every manual_ca kind in workResolutions for replay_mismatch', () => {
    const work = workResolutions(
      row({ issues: [{ type: 'corporate_action_manual', reason: 'replay_mismatch' }] }),
    )
    expect(work.filter((x) => x.id === 'manual_ca').map((x) => x.label)).toEqual([
      'Bonus',
      'Split',
      'Merger',
      'Demerger',
      'Rights issue',
      'Buyback',
    ])
    expect(
      metaResolutions(
        row({ issues: [{ type: 'corporate_action_manual', reason: 'replay_mismatch' }] }),
      ),
    ).toEqual([expect.objectContaining({ id: 'acknowledge' })])
  })

  it('keeps merger and remap in workResolutions on ledger_position_not_in_holdings', () => {
    const r = row({
      status: 'full_history',
      issues: [{ type: 'corporate_action_manual', reason: 'ledger_position_not_in_holdings' }],
    })
    expect(workResolutions(r).map((x) => x.label)).toEqual([
      'Resolve as merger',
      'Remap ISIN (1:1)',
    ])
    expect(metaResolutions(r)).toEqual([])
  })

  it('splits work vs meta for opening_lot_needed', () => {
    const r = row({
      status: 'snapshot_only',
      issues: [{ type: 'opening_lot_needed', holding_units: '50' }],
    })
    expect(workResolutions(r).map((x) => x.openingLotClassification)).toEqual([
      'ipo_allotment',
      'transfer_in',
      'demerger_result',
    ])
    expect(metaResolutions(r)).toEqual([])
  })
})
