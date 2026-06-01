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
    tooltip: 'Full transaction history. Schedule 112A available.',
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
      'Net-worth tracked, but no transaction history — no tax computation. Add via CSV or manual entry to enable the Tax Pack.',
    severity: 'warn',
    taxReady: false,
  },
  mismatch: {
    status: 'mismatch',
    icon: 'pi pi-times-circle',
    label: 'Reconcile needed',
    tooltip:
      'Units in transactions differ from the eCAS observation. Schedule 112A is skipped until resolved.',
    severity: 'critical',
    taxReady: false,
  },
  user_acknowledged: {
    status: 'user_acknowledged',
    icon: 'pi pi-minus-circle',
    label: 'Acknowledged',
    tooltip: 'You marked this gap as known. Excluded from the tax export.',
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
