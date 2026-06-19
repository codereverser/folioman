/**
 * Scenario → applicable-resolution map for the integrity page.
 *
 * Single source of truth: each integrity issue (and a few status-only cases)
 * maps to the set of remediation actions that fit it. IntegrityView routes
 * button clicks through here instead of hard-coding one button per issue type.
 */
import type { ManualCaKind } from '@/integrity/manualCorporateAction'
import type { IntegrityStatus } from '@/integrity/status'
import {
  corporateActionSuggestions,
  hasIncompleteHistory,
  openingLotIssue,
} from '@/integrity/status'
import type { IntegrityRow } from '@/stores/integrity'

export type OpeningLotClassification = 'ipo_allotment' | 'transfer_in' | 'demerger_result'

/** A user-triggerable remediation on an integrity row. */
export type ResolutionId =
  | 'apply_ca_suggestion'
  | 'opening_lot'
  | 'identity_remap'
  | 'manual_ca'
  | 'fetch_corporate_actions'
  | 'acknowledge'
  | 'unacknowledge'

export interface Resolution {
  id: ResolutionId
  label: string
  icon: string
  /** Opening-lot dialog preset (``opening_lot`` only). */
  openingLotClassification?: OpeningLotClassification
  /** Manual-CA dialog preset (``manual_ca`` only). */
  manualCaKind?: ManualCaKind
  /** False when the map lists a future path with no handler yet. */
  available?: boolean
}

export interface ResolutionContext {
  status: IntegrityStatus
  issues: Record<string, unknown>[]
  /** Equity corporate-action cache timestamp; null = never fetched. */
  caSyncedAt: string | null
}

const OPENING_LOT_RESOLUTIONS: Resolution[] = [
  {
    id: 'opening_lot',
    label: 'IPO allotment',
    icon: 'pi pi-plus-circle',
    openingLotClassification: 'ipo_allotment',
  },
  {
    id: 'opening_lot',
    label: 'Transfer in',
    icon: 'pi pi-plus-circle',
    openingLotClassification: 'transfer_in',
  },
  {
    id: 'opening_lot',
    label: 'Demerger receipt',
    icon: 'pi pi-plus-circle',
    openingLotClassification: 'demerger_result',
  },
]

function manualCa(label: string, kind: ManualCaKind, icon = 'pi pi-wrench'): Resolution {
  return { id: 'manual_ca', label, icon, manualCaKind: kind }
}

/** Manual corporate-action kinds offered for a ``corporate_action_manual`` reason. */
const MANUAL_CA_BY_REASON: Record<string, Resolution[]> = {
  ledger_position_not_in_holdings: [
    manualCa('Resolve as merger', 'merger', 'pi pi-arrow-right-arrow-left'),
    { id: 'identity_remap', label: 'Remap ISIN (1:1)', icon: 'pi pi-arrow-right-arrow-left' },
  ],
  holding_below_ledger: [
    manualCa('Record buyback', 'buyback'),
    {
      id: 'manual_ca',
      label: 'Transfer out',
      icon: 'pi pi-sign-out',
      manualCaKind: 'buyback',
      available: false,
    },
  ],
  incomplete_history: [],
  snapshot_only: OPENING_LOT_RESOLUTIONS,
  non_integer_ratio: [
    manualCa('Bonus', 'bonus'),
    manualCa('Split', 'split'),
    manualCa('Merger', 'merger'),
  ],
  ratio_without_matching_event: [
    manualCa('Bonus', 'bonus'),
    manualCa('Split', 'split'),
    manualCa('Merger', 'merger'),
  ],
  replay_mismatch: [
    manualCa('Bonus', 'bonus'),
    manualCa('Split', 'split'),
    manualCa('Merger', 'merger'),
    manualCa('Rights issue', 'rights'),
    manualCa('Buyback', 'buyback'),
  ],
  no_matching_event: [
    manualCa('Bonus', 'bonus'),
    manualCa('Split', 'split'),
    manualCa('Merger', 'merger'),
    manualCa('Rights issue', 'rights'),
    manualCa('Buyback', 'buyback'),
  ],
}

const DEFAULT_MISMATCH_MANUAL: Resolution[] = [
  manualCa('Bonus', 'bonus'),
  manualCa('Split', 'split'),
  manualCa('Merger', 'merger'),
  manualCa('Rights issue', 'rights'),
  manualCa('Buyback', 'buyback'),
]

function dedupeResolutions(items: Resolution[]): Resolution[] {
  const seen = new Set<string>()
  const out: Resolution[] = []
  for (const r of items) {
    const key = [r.id, r.openingLotClassification ?? '', r.manualCaKind ?? '', r.label].join('|')
    if (seen.has(key)) continue
    seen.add(key)
    out.push(r)
  }
  return out
}

function manualReasons(issues: Record<string, unknown>[]): string[] {
  return issues
    .filter((i) => i.type === 'corporate_action_manual')
    .map((i) => String(i.reason ?? ''))
    .filter(Boolean)
}

/** All resolutions that fit this row's issues and status (including unavailable). */
export function applicableResolutions(ctx: ResolutionContext): Resolution[] {
  const { status, issues, caSyncedAt } = ctx

  if (status === 'user_acknowledged') {
    return [{ id: 'unacknowledge', label: 'Un-acknowledge', icon: 'pi pi-undo' }]
  }

  const suggestions = corporateActionSuggestions(issues)
  if (suggestions.length > 0) {
    return [
      {
        id: 'apply_ca_suggestion',
        label: suggestions.length > 1 ? 'Apply actions' : 'Apply action',
        icon: 'pi pi-bolt',
      },
    ]
  }

  // Checked before the incomplete-history bail-out: a row can be both (orphan sells
  // in the tradebook AND an eCAS-only holding that needs a classified opening lot,
  // e.g. units received in a merger). The opening lot is still actionable.
  if (openingLotIssue(issues)) {
    return OPENING_LOT_RESOLUTIONS
  }

  // Incomplete history with nothing else actionable has no in-app fix — the user
  // must import the missing earlier transactions.
  if (hasIncompleteHistory(issues)) {
    return []
  }

  // A reconciled row needs nothing — never surface manual-CA authoring on it. Guards
  // against a stale `corporate_action_manual` annotation lingering after a CA was
  // applied and the units already tie out.
  if (status === 'reconciled') {
    return []
  }

  const out: Resolution[] = []
  for (const reason of manualReasons(issues)) {
    const mapped = MANUAL_CA_BY_REASON[reason]
    if (mapped) out.push(...mapped)
  }

  if (status === 'mismatch') {
    const needsFetch = !caSyncedAt && out.length === 0
    if (needsFetch) {
      out.push({
        id: 'fetch_corporate_actions',
        label: 'Fetch corporate actions',
        icon: 'pi pi-cloud-download',
      })
    } else if (out.length === 0) {
      out.push(...DEFAULT_MISMATCH_MANUAL)
    }
    out.push({ id: 'acknowledge', label: 'Acknowledge', icon: 'pi pi-minus-circle' })
  }

  return dedupeResolutions(out)
}

/** Resolutions the UI can act on today (drops ``available: false`` entries). */
export function actionableResolutions(ctx: ResolutionContext): Resolution[] {
  return applicableResolutions(ctx).filter((r) => r.available !== false)
}

export function resolutionsForRow(row: IntegrityRow): Resolution[] {
  return actionableResolutions({
    status: row.status,
    issues: row.issues,
    caSyncedAt: row.caSyncedAt,
  })
}

/** Primary remediation — first actionable resolution excluding acknowledge. */
export function primaryResolution(row: IntegrityRow): Resolution | null {
  return (
    resolutionsForRow(row).find((r) => r.id !== 'acknowledge' && r.id !== 'unacknowledge') ?? null
  )
}

export function acknowledgeResolution(row: IntegrityRow): Resolution | null {
  return resolutionsForRow(row).find((r) => r.id === 'acknowledge') ?? null
}

/** Remediation paths shown inside the per-row “Resolve…” menu. */
export function workResolutions(row: IntegrityRow): Resolution[] {
  return resolutionsForRow(row).filter((r) => r.id !== 'acknowledge' && r.id !== 'unacknowledge')
}

/** Secondary row actions kept outside the Resolve menu (acknowledge / undo). */
export function metaResolutions(row: IntegrityRow): Resolution[] {
  return resolutionsForRow(row).filter((r) => r.id === 'acknowledge' || r.id === 'unacknowledge')
}
