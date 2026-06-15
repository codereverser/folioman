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
