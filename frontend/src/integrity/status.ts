/**
 * Data-integrity vocabulary — the trust differentiator.
 *
 * The backend's IntegrityStatusOut.status is a free string; we map it to a known
 * set with consistent glyph / label / colour / severity. Teal = "verified" ties
 * the brand colour to the core differentiator (green/red stay reserved for P&L).
 */
export type IntegrityStatus =
  | 'full_history'
  | 'reconciled'
  | 'snapshot_only'
  | 'mismatch'
  | 'user_acknowledged'
  | 'unknown'

export type IntegritySeverity = 'verified' | 'warn' | 'critical' | 'neutral'

export interface IntegrityMeta {
  status: IntegrityStatus
  icon: string // primeicons class
  label: string
  tooltip: string
  severity: IntegritySeverity
  /** Counts toward the tax-ready set. */
  taxReady: boolean
}

const META: Record<IntegrityStatus, IntegrityMeta> = {
  full_history: {
    status: 'full_history',
    icon: 'pi pi-verified',
    label: 'Full history',
    tooltip: 'Full transaction history — a capital-gains worksheet is available.',
    severity: 'verified',
    taxReady: true,
  },
  reconciled: {
    status: 'reconciled',
    icon: 'pi pi-check-circle',
    label: 'Reconciled',
    tooltip: 'Transactions match the eCAS snapshot. High confidence.',
    severity: 'verified',
    taxReady: true,
  },
  snapshot_only: {
    status: 'snapshot_only',
    icon: 'pi pi-exclamation-triangle',
    label: 'Snapshot only',
    tooltip:
      'Net worth is tracked from the statement, but there’s no transaction history — so no capital-gains worksheet.',
    severity: 'warn',
    taxReady: false,
  },
  mismatch: {
    status: 'mismatch',
    icon: 'pi pi-times-circle',
    label: 'Reconcile needed',
    tooltip:
      'Ledger units differ from the latest statement’s holdings. The capital-gains worksheet skips this until it’s resolved.',
    severity: 'critical',
    taxReady: false,
  },
  user_acknowledged: {
    status: 'user_acknowledged',
    icon: 'pi pi-minus-circle',
    label: 'Acknowledged',
    tooltip: 'You marked this gap as known. Left out of the capital-gains worksheet.',
    severity: 'neutral',
    taxReady: false,
  },
  unknown: {
    status: 'unknown',
    icon: 'pi pi-question-circle',
    label: 'Unknown',
    tooltip: 'Integrity not yet computed for this security.',
    severity: 'neutral',
    taxReady: false,
  },
}

const KNOWN = new Set<string>([
  'full_history',
  'reconciled',
  'snapshot_only',
  'mismatch',
  'user_acknowledged',
])

/** Normalise an arbitrary backend status string to a known IntegrityStatus. */
export function toIntegrityStatus(raw: string | null | undefined): IntegrityStatus {
  const v = (raw ?? '').toLowerCase()
  return KNOWN.has(v) ? (v as IntegrityStatus) : 'unknown'
}

export function integrityMeta(status: IntegrityStatus): IntegrityMeta {
  return META[status]
}

/** A mid-history tradebook with orphan sells (PartialBlock). */
export function hasIncompleteHistory(issues: Record<string, unknown>[]): boolean {
  return issues.some((i) => i.type === 'incomplete_history')
}

export function incompleteHistoryReason(issues: Record<string, unknown>[]): string | null {
  if (!hasIncompleteHistory(issues)) return null
  return (
    'Incomplete transaction history — sells without matching buys, so no ' +
    'capital-gains worksheet until an earlier tradebook supplies the missing purchases.'
  )
}

export function incompleteHistoryFix(): string {
  return 'Import an earlier-period tradebook that includes the missing buy transactions.'
}

/** One event within a corporate-action suggestion. */
export interface CorporateActionEvent {
  actionType: string
  subject: string
  exDate: string
  unitMultiplier: string
}

/** A high-confidence corporate-action suggestion from reconciliation. A unit gap
 * can need several events applied together (e.g. two splits), so this carries the
 * whole ordered set. */
export interface CorporateActionSuggestion {
  referenceIds: number[]
  events: CorporateActionEvent[]
}

function asRecord(issue: Record<string, unknown>): Record<string, unknown> {
  return issue
}

export function corporateActionSuggestions(
  issues: Record<string, unknown>[],
): CorporateActionSuggestion[] {
  return issues
    .filter((i) => i.type === 'corporate_action_suggestion')
    .map((raw) => {
      const i = asRecord(raw)
      const referenceIds = Array.isArray(i.reference_ids)
        ? i.reference_ids.map(Number).filter((n) => Number.isFinite(n) && n > 0)
        : []
      const events: CorporateActionEvent[] = Array.isArray(i.events)
        ? i.events.map((rawEvent) => {
            const e = asRecord(rawEvent as Record<string, unknown>)
            return {
              actionType: String(e.action_type ?? ''),
              subject: String(e.subject ?? ''),
              exDate: String(e.ex_date ?? ''),
              unitMultiplier: String(e.unit_multiplier ?? ''),
            }
          })
        : []
      return { referenceIds, events }
    })
    .filter((s) => s.referenceIds.length > 0)
}

export function hasCorporateActionSuggestion(issues: Record<string, unknown>[]): boolean {
  return corporateActionSuggestions(issues).length > 0
}

const MANUAL_CA_COPY: Record<string, string> = {
  incomplete_history:
    'A unit gap might be a bonus or split, but transaction history is incomplete — review the corporate action manually.',
  snapshot_only:
    'Holdings are snapshot-only — corporate actions cannot be matched against a tradebook ledger.',
  ledger_position_not_in_holdings:
    'The ledger shows units this eCAS snapshot does not list — often a pre-merger ISIN or an off-book transfer. Enter the merger or opening lot manually.',
  holding_below_ledger:
    'Statement holdings are below the ledger — check for an off-market transfer or a trade on another broker.',
  non_integer_ratio:
    'The unit gap is not a clean bonus/split ratio — review corporate actions manually.',
  ratio_without_matching_event:
    'The unit ratio does not match any cached corporate action — refresh the feed or enter the event manually.',
  replay_mismatch:
    'The cached corporate actions don’t reconcile to your holdings — the feed may be incomplete or one applies differently. Review and enter the events manually.',
  no_matching_event:
    'A unit gap remains but no cached corporate action explains it — refresh the feed or enter the event manually.',
}

/** User-facing copy for a manual corporate-action flag, if any. */
export function corporateActionManualNote(issues: Record<string, unknown>[]): string | null {
  const manual = issues.find((i) => i.type === 'corporate_action_manual')
  if (!manual) return null
  const reason = String(manual.reason ?? '')
  const base = MANUAL_CA_COPY[reason]
  if (base) return base
  if (reason === 'ratio_without_matching_event' && manual.unit_ratio) {
    return `${MANUAL_CA_COPY.ratio_without_matching_event} (ratio ×${manual.unit_ratio}).`
  }
  return 'This unit gap needs a manual corporate-action review.'
}

function describeEvent(e: CorporateActionEvent): string {
  const when = e.exDate ? ` (ex ${e.exDate})` : ''
  return `${e.subject}${when} — ×${e.unitMultiplier}`
}

export function corporateActionSuggestionSummary(s: CorporateActionSuggestion): string {
  if (s.events.length === 1) {
    return `Suggested corporate action: ${describeEvent(s.events[0])} unit adjustment.`
  }
  return `Suggested corporate actions (${s.events.length}): ${s.events.map(describeEvent).join('; ')}.`
}

export function corporateActionApplyConfirmMessage(
  s: CorporateActionSuggestion,
  name: string,
): string {
  const what =
    s.events.length === 1
      ? `"${s.events[0].subject}"`
      : `${s.events.length} corporate actions (${s.events.map((e) => e.subject).join(', ')})`
  return (
    `Apply ${what} to "${name}"? ` +
    'This writes bonus/split ledger rows and re-reconciles the folio. ' +
    'You can undo only by editing the ledger — review before confirming.'
  )
}

export interface OpeningLotIssue {
  holdingUnits: string
}

export function openingLotIssue(issues: Record<string, unknown>[]): OpeningLotIssue | null {
  const raw = issues.find((i) => i.type === 'opening_lot_needed')
  if (!raw) return null
  return { holdingUnits: String(raw.holding_units ?? '') }
}

export function openingLotSummary(issue: OpeningLotIssue): string {
  return (
    `eCAS shows ${issue.holdingUnits} units with no tradebook history — ` +
    'record how they were acquired (IPO, transfer-in, or demerger receipt).'
  )
}

export const OPENING_LOT_CLASSIFICATIONS = [
  { value: 'ipo_allotment', label: 'IPO allotment' },
  { value: 'transfer_in', label: 'Transfer in' },
  { value: 'demerger_result', label: 'Demerger receipt' },
] as const

export function needsIdentityRemap(issues: Record<string, unknown>[]): boolean {
  return issues.some(
    (i) => i.type === 'corporate_action_manual' && i.reason === 'ledger_position_not_in_holdings',
  )
}

// One canonical remediation for an incomplete mutual-fund ledger — the same advice
// the Import screen gives, so guidance never contradicts itself.
const REIMPORT_FIX = 'Re-import a since-inception (Detailed) CAS that includes zero-balance folios.'

/**
 * The concrete next step for a status, or null when none is needed (already
 * tax-ready). A demat/eCAS snapshot is inherent — the depository statement
 * carries no transaction history — so re-importing it changes nothing; we say so
 * rather than send the user on a futile errand.
 */
export function remediation(
  status: IntegrityStatus,
  { folioType = '' }: { folioType?: string } = {},
): string | null {
  switch (status) {
    case 'mismatch':
      return REIMPORT_FIX
    case 'snapshot_only':
      return folioType === 'demat'
        ? 'Demat statements carry no transaction history, so there’s no capital-gains worksheet for this holding yet.'
        : REIMPORT_FIX
    case 'user_acknowledged':
      return 'Marked as a known gap — left out of the worksheet. Un-acknowledge to track it again.'
    default:
      return null
  }
}

export interface IntegrityRollup {
  total: number
  verified: number
  snapshot: number
  mismatch: number
  acknowledged: number
  taxReady: number
  /** Items the user can act on right now (mismatches). */
  needsAttention: number
}

/** Aggregate a set of statuses into the dashboard health summary. */
export function rollupIntegrity(statuses: IntegrityStatus[]): IntegrityRollup {
  const r: IntegrityRollup = {
    total: statuses.length,
    verified: 0,
    snapshot: 0,
    mismatch: 0,
    acknowledged: 0,
    taxReady: 0,
    needsAttention: 0,
  }
  for (const s of statuses) {
    const meta = META[s]
    if (meta.taxReady) r.taxReady += 1
    if (meta.severity === 'verified') r.verified += 1
    else if (s === 'snapshot_only') r.snapshot += 1
    else if (s === 'mismatch') {
      r.mismatch += 1
      r.needsAttention += 1
    } else if (s === 'user_acknowledged') r.acknowledged += 1
  }
  return r
}
