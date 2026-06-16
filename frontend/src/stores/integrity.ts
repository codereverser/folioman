import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api, type Schemas } from '@/api/client'
import {
  rollupIntegrity,
  toIntegrityStatus,
  type IntegrityRollup,
  type IntegrityStatus,
} from '@/integrity/status'

export interface IntegrityRow {
  securityId: number
  folioId: number
  name: string
  isin: string
  securityType: string
  folioNumber: string
  folioType: string
  broker: string
  status: IntegrityStatus
  taxSafe: boolean
  unitsFromHoldings: string | null
  unitsFromTransactions: string | null
  issues: Record<string, unknown>[]
  ledgerThrough: string | null
  snapshotAsOf: string | null
  lastReconciledAt: string | null
}

function toRow(r: Schemas['IntegrityStatusOut']): IntegrityRow {
  return {
    securityId: r.security.id,
    folioId: r.folio.id,
    name: r.security.name ?? r.security.isin,
    isin: r.security.isin,
    securityType: r.security.security_type,
    folioNumber: r.folio.number,
    folioType: r.folio.folio_type,
    broker: r.folio.broker,
    status: toIntegrityStatus(r.status),
    taxSafe: r.tax_safe,
    unitsFromHoldings: r.units_from_holdings,
    unitsFromTransactions: r.units_from_transactions,
    issues: r.issues ?? [],
    ledgerThrough: r.ledger_through,
    snapshotAsOf: r.snapshot_as_of,
    lastReconciledAt: r.last_reconciled_at,
  }
}

/**
 * Per-investor integrity statuses, cached by investor id. One row per
 * (security, folio) — the reconciliation grain the backend reports. Loads
 * `/investors/{id}/integrity`, rolls the statuses up for the dashboard health
 * card, and owns the two mutations the Integrity page needs:
 *  - `acknowledge` — accept a known mismatch (it stays out of the tax worksheet);
 *  - `recompute` — force a full server-side re-reconcile.
 *
 * Both mutations update the cache in place so the dashboard rollup and the
 * Integrity page react without a manual refetch.
 *
 * Fails soft: on error (e.g. no backend in dev) the investor's entry stays
 * undefined so callers fall back to placeholder data.
 */
export const useIntegrityStore = defineStore('integrity', () => {
  const byInvestor = ref<Record<number, IntegrityRow[]>>({})
  const loading = ref(false)
  const acknowledging = ref(false)
  const applyingCorporateAction = ref(false)
  const error = ref<string | null>(null)

  function rowsFor(investorId: number): IntegrityRow[] {
    return byInvestor.value[investorId] ?? []
  }

  function rollupFor(investorId: number): IntegrityRollup {
    return rollupIntegrity(rowsFor(investorId).map((r) => r.status))
  }

  async function load(investorId: number, { force = false } = {}): Promise<void> {
    if (!force && byInvestor.value[investorId]) return
    loading.value = true
    error.value = null
    try {
      const { data, error: apiError } = await api.GET('/api/investors/{investor_id}/integrity', {
        params: { path: { investor_id: investorId } },
      })
      if (apiError || !data) throw new Error('integrity request failed')
      byInvestor.value[investorId] = data.map(toRow)
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
    } finally {
      loading.value = false
    }
  }

  /** Force a full server-side re-reconcile and replace the cached rows. */
  async function recompute(investorId: number): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const { data, error: apiError } = await api.POST(
        '/api/investors/{investor_id}/integrity/recompute',
        { params: { path: { investor_id: investorId } } },
      )
      if (apiError || !data) throw new Error('integrity recompute failed')
      byInvestor.value[investorId] = data.map(toRow)
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
    } finally {
      loading.value = false
    }
  }

  /**
   * Accept a known mismatch: the user marks the gap as reviewed. The security
   * stays tax-unsafe (out of the worksheet) — this dismisses the red flag, it
   * does not fix the units. Updates the matching cached row in place.
   */
  function patchRow(investorId: number, data: Schemas['IntegrityStatusOut']): void {
    const rows = byInvestor.value[investorId]
    if (!rows) return
    const i = rows.findIndex(
      (r) => r.securityId === data.security.id && r.folioId === data.folio.id,
    )
    if (i !== -1) rows[i] = toRow(data)
  }

  async function acknowledge(
    investorId: number,
    securityId: number,
    folioId: number,
  ): Promise<boolean> {
    acknowledging.value = true
    error.value = null
    try {
      const { data, error: apiError } = await api.POST(
        '/api/investors/{investor_id}/integrity/{security_id}/{folio_id}/acknowledge',
        {
          params: {
            path: { investor_id: investorId, security_id: securityId, folio_id: folioId },
          },
        },
      )
      if (apiError || !data) throw new Error('acknowledge failed')
      patchRow(investorId, data)
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      return false
    } finally {
      acknowledging.value = false
    }
  }

  /** Undo an acknowledgement: the row reverts to its real status (an unresolved
   *  gap reappears as a mismatch). Updates the cached row in place. */
  async function unacknowledge(
    investorId: number,
    securityId: number,
    folioId: number,
  ): Promise<boolean> {
    acknowledging.value = true
    error.value = null
    try {
      const { data, error: apiError } = await api.POST(
        '/api/investors/{investor_id}/integrity/{security_id}/{folio_id}/unacknowledge',
        {
          params: {
            path: { investor_id: investorId, security_id: securityId, folio_id: folioId },
          },
        },
      )
      if (apiError || !data) throw new Error('unacknowledge failed')
      patchRow(investorId, data)
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      return false
    } finally {
      acknowledging.value = false
    }
  }

  /** Apply one cached corporate-action reference; patch the row from the response. */
  async function applyCorporateAction(
    investorId: number,
    securityId: number,
    folioId: number,
    referenceId: number,
  ): Promise<boolean> {
    applyingCorporateAction.value = true
    error.value = null
    try {
      const { data, error: apiError } = await api.POST(
        '/api/investors/{investor_id}/integrity/{security_id}/{folio_id}/apply-corporate-action',
        {
          params: {
            path: { investor_id: investorId, security_id: securityId, folio_id: folioId },
          },
          body: { reference_id: referenceId },
        },
      )
      if (apiError || !data?.integrity) throw new Error('apply corporate action failed')
      patchRow(investorId, data.integrity)
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      return false
    } finally {
      applyingCorporateAction.value = false
    }
  }

  function clear(): void {
    byInvestor.value = {}
  }

  return {
    byInvestor,
    loading,
    acknowledging,
    applyingCorporateAction,
    error,
    rowsFor,
    rollupFor,
    load,
    recompute,
    acknowledge,
    unacknowledge,
    applyCorporateAction,
    clear,
  }
})
