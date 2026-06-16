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

/** A high-confidence corporate-action suggestion from reconciliation. */
export interface CorporateActionSuggestion {
  referenceId: number
  actionType: string
  subject: string
  exDate: string
  unitMultiplier: string
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
      return {
        referenceId: Number(i.reference_id),
        actionType: String(i.action_type ?? ''),
        subject: String(i.subject ?? ''),
        exDate: String(i.ex_date ?? ''),
        unitMultiplier: String(i.unit_multiplier ?? ''),
      }
    })
    .filter((s) => Number.isFinite(s.referenceId) && s.referenceId > 0)
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

export function corporateActionSuggestionSummary(s: CorporateActionSuggestion): string {
  const when = s.exDate ? ` (ex ${s.exDate})` : ''
  return `Suggested corporate action: ${s.subject}${when} — applies a ×${s.unitMultiplier} unit adjustment.`
}

export function corporateActionApplyConfirmMessage(
  s: CorporateActionSuggestion,
  name: string,
): string {
  return (
    `Apply "${s.subject}" to "${name}"? ` +
    'This writes bonus/split ledger rows and re-reconciles the folio. ' +
    'You can undo only by editing the ledger — review before confirming.'
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
