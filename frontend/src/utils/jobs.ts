// Shared formatting for import-job + valuation status, used by the Settings
// "Jobs & valuation" panel and the Import screen's recent-imports list.
import type { Schemas } from '@/api/client'

export type JobStatusTone = 'ok' | 'warn' | 'bad' | 'busy'

// Maps both import-job statuses and valuation statuses to a visual tone.
const STATUS_TONE: Record<string, JobStatusTone> = {
  success: 'ok',
  ready: 'ok',
  failed: 'bad',
  error: 'bad',
  completed_with_warnings: 'warn',
  needs_confirmation: 'warn',
  pending: 'busy',
  running: 'busy',
  computing: 'busy',
}

const STATUS_LABEL: Record<string, string> = {
  success: 'Success',
  failed: 'Failed',
  completed_with_warnings: 'Warnings',
  needs_confirmation: 'Needs confirmation',
  pending: 'Pending',
  running: 'Running',
  ready: 'Ready',
  computing: 'Computing',
  error: 'Error',
}

export const jobStatusTone = (status: string): JobStatusTone => STATUS_TONE[status] ?? 'busy'
export const jobStatusLabel = (status: string): string => STATUS_LABEL[status] ?? status

/** A one-line outcome for an import row: the error if it failed, else what landed. */
export function importSummary(job: Schemas['ImportJobSummaryOut']): string {
  if (job.error) return job.error
  const r = (job.result ?? {}) as Record<string, number | undefined>
  const parts: string[] = []
  if (r.transactions_created) parts.push(`${r.transactions_created} transactions`)
  if (r.holdings_snapshotted) parts.push(`${r.holdings_snapshotted} snapshots`)
  if (r.holdings_created) parts.push(`${r.holdings_created} holdings`)
  if (r.securities) parts.push(`${r.securities} securities`)
  return parts.join(' · ') || '—'
}
