import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api, type Schemas } from '@/api/client'
import {
  rollupIntegrity,
  toIntegrityStatus,
  rowNeedsAttention,
  type IntegrityRollup,
  type IntegrityStatus,
} from '@/integrity/status'

export type ManualCorporateActionBody = Schemas['ManualCorporateActionIn']
export type SecurityOption = Schemas['SecurityRef']

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
  /** When this equity's corporate actions were last fetched (null = never). */
  caSyncedAt: string | null
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
    caSyncedAt: r.security.corporate_actions_synced_at ?? null,
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
  const securitiesByInvestor = ref<Record<number, SecurityOption[]>>({})
  const loading = ref(false)
  const acknowledging = ref(false)
  const applyingCorporateAction = ref(false)
  const applyingManualCorporateAction = ref(false)
  const refreshingCorporateActions = ref(false)
  const recordingOpeningLot = ref(false)
  const applyingIdentityRemap = ref(false)
  const error = ref<string | null>(null)
  // The parent a demerger receipt was just linked to (its cost basis was reduced),
  // surfaced so the UI can confirm what happened to the parent. Null when none matched.
  const suggestedParent = ref<{ id: number; name: string; isin: string } | null>(null)

  function rowsFor(investorId: number): IntegrityRow[] {
    return byInvestor.value[investorId] ?? []
  }

  function rollupFor(investorId: number): IntegrityRollup {
    const rows = rowsFor(investorId)
    const rollup = rollupIntegrity(rows.map((r) => r.status))
    rollup.needsAttention = rows.filter((r) => rowNeedsAttention(r.status, r.issues)).length
    return rollup
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

  /** Fetch NSE/BSE corporate actions for this investor's mismatched equities now,
   * then replace the cached rows with the re-reconciled result (suggestions appear). */
  async function refreshCorporateActions(investorId: number): Promise<boolean> {
    refreshingCorporateActions.value = true
    error.value = null
    try {
      const { data, error: apiError } = await api.POST(
        '/api/investors/{investor_id}/integrity/refresh-corporate-actions',
        { params: { path: { investor_id: investorId } } },
      )
      if (apiError || !data) throw new Error('could not fetch corporate actions')
      byInvestor.value[investorId] = data.map(toRow)
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      return false
    } finally {
      refreshingCorporateActions.value = false
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

  /** Apply the cached corporate-action references that close a gap (one or several);
   * patch the row from the response. */
  async function applyCorporateAction(
    investorId: number,
    securityId: number,
    folioId: number,
    referenceIds: number[],
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
          body: { reference_ids: referenceIds },
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

  async function recordOpeningLot(
    investorId: number,
    securityId: number,
    folioId: number,
    body: {
      classification: string
      date: string
      price?: string
      cost_basis_unknown?: boolean
    },
  ): Promise<boolean> {
    recordingOpeningLot.value = true
    error.value = null
    try {
      const { data, error: apiError } = await api.POST(
        '/api/investors/{investor_id}/integrity/{security_id}/{folio_id}/record-opening-lot',
        {
          params: {
            path: { investor_id: investorId, security_id: securityId, folio_id: folioId },
          },
          body: {
            classification: body.classification,
            date: body.date,
            price: body.price ? Number(body.price) : undefined,
            cost_basis_unknown: body.cost_basis_unknown ?? false,
          },
        },
      )
      if (apiError || !data?.integrity) throw new Error('record opening lot failed')
      patchRow(investorId, data.integrity)
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      return false
    } finally {
      recordingOpeningLot.value = false
    }
  }

  async function recordOpeningLots(
    investorId: number,
    securityId: number,
    folioId: number,
    body: {
      classification: string
      lots: { date: string; units: string; price?: string }[]
      cost_basis_unknown?: boolean
      demerger_date?: string
    },
  ): Promise<boolean> {
    recordingOpeningLot.value = true
    error.value = null
    suggestedParent.value = null
    try {
      const { data, error: apiError } = await api.POST(
        '/api/investors/{investor_id}/integrity/{security_id}/{folio_id}/record-opening-lots',
        {
          params: {
            path: { investor_id: investorId, security_id: securityId, folio_id: folioId },
          },
          body: {
            classification: body.classification,
            lots: body.lots.map((l) => ({
              date: l.date,
              units: Number(l.units),
              price: l.price ? Number(l.price) : undefined,
            })),
            cost_basis_unknown: body.cost_basis_unknown ?? false,
            demerger_date: body.demerger_date || undefined,
          },
        },
      )
      if (apiError) throw new Error('record opening lots failed')
      suggestedParent.value = data?.suggested_parent ?? null
      // A fully-sold child reconciles to net 0 and may drop its status row — refetch
      // rather than patch. A still-held child returns the updated row.
      if (data?.integrity) patchRow(investorId, data.integrity)
      else await load(investorId, { force: true })
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      return false
    } finally {
      recordingOpeningLot.value = false
    }
  }

  async function removeOpeningLot(
    investorId: number,
    securityId: number,
    folioId: number,
  ): Promise<boolean> {
    recordingOpeningLot.value = true
    error.value = null
    try {
      const { data, error: apiError } = await api.POST(
        '/api/investors/{investor_id}/integrity/{security_id}/{folio_id}/remove-opening-lot',
        {
          params: { path: { investor_id: investorId, security_id: securityId, folio_id: folioId } },
        },
      )
      if (apiError) throw new Error('remove opening lot failed')
      // The holding may revert to snapshot-only (still in eCAS) or drop its row — patch
      // when a status comes back, else refetch.
      if (data?.integrity) patchRow(investorId, data.integrity)
      else await load(investorId, { force: true })
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      return false
    } finally {
      recordingOpeningLot.value = false
    }
  }

  async function applyIdentityRemap(
    investorId: number,
    securityId: number,
    folioId: number,
    body: { to_isin: string; to_symbol?: string; to_name?: string },
  ): Promise<boolean> {
    applyingIdentityRemap.value = true
    error.value = null
    try {
      const { data, error: apiError } = await api.POST(
        '/api/investors/{investor_id}/integrity/{security_id}/{folio_id}/apply-identity-remap',
        {
          params: {
            path: { investor_id: investorId, security_id: securityId, folio_id: folioId },
          },
          body: {
            to_isin: body.to_isin,
            to_symbol: body.to_symbol ?? '',
            to_name: body.to_name ?? '',
          },
        },
      )
      if (apiError || !data?.integrity) throw new Error('identity remap failed')
      patchRow(investorId, data.integrity)
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      return false
    } finally {
      applyingIdentityRemap.value = false
    }
  }

  async function applyManualCorporateAction(
    investorId: number,
    securityId: number,
    folioId: number,
    body: ManualCorporateActionBody,
  ): Promise<boolean> {
    applyingManualCorporateAction.value = true
    error.value = null
    try {
      const { data, error: apiError } = await api.POST(
        '/api/investors/{investor_id}/integrity/{security_id}/{folio_id}/apply-manual-corporate-action',
        {
          params: {
            path: { investor_id: investorId, security_id: securityId, folio_id: folioId },
          },
          body,
        },
      )
      if (apiError || !data?.integrity) throw new Error('could not apply the corporate action')
      patchRow(investorId, data.integrity)
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      return false
    } finally {
      applyingManualCorporateAction.value = false
    }
  }

  function securitiesFor(investorId: number): SecurityOption[] {
    return securitiesByInvestor.value[investorId] ?? []
  }

  /** Load the investor's securities once (for the merger/demerger acquirer picker). */
  async function loadSecurities(investorId: number, { force = false } = {}): Promise<void> {
    if (!force && securitiesByInvestor.value[investorId]) return
    const { data, error: apiError } = await api.GET('/api/investors/{investor_id}/securities', {
      params: { path: { investor_id: investorId } },
    })
    if (apiError || !data) return
    securitiesByInvestor.value[investorId] = data
  }

  function clear(): void {
    byInvestor.value = {}
    securitiesByInvestor.value = {}
  }

  return {
    byInvestor,
    loading,
    acknowledging,
    applyingCorporateAction,
    applyingManualCorporateAction,
    refreshingCorporateActions,
    recordingOpeningLot,
    applyingIdentityRemap,
    error,
    suggestedParent,
    rowsFor,
    rollupFor,
    securitiesFor,
    loadSecurities,
    load,
    refreshCorporateActions,
    recompute,
    acknowledge,
    unacknowledge,
    applyCorporateAction,
    applyManualCorporateAction,
    recordOpeningLot,
    recordOpeningLots,
    removeOpeningLot,
    applyIdentityRemap,
    clear,
  }
})
