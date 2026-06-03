import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api/client'
import {
  rollupIntegrity,
  toIntegrityStatus,
  type IntegrityRollup,
  type IntegrityStatus,
} from '@/integrity/status'

export interface IntegrityRow {
  securityId: number
  name: string
  isin: string
  status: IntegrityStatus
  taxSafe: boolean
  unitsFromHoldings: string | null
  unitsFromTransactions: string | null
  lastReconciledAt: string | null
}

/**
 * Per-investor integrity statuses, cached by investor id. This is the seed of
 * the reconciliation store: it loads `/investors/{id}/integrity` and rolls the
 * statuses up for the dashboard health card. Acknowledge / refresh mutations
 * and the standalone Integrity page hang off this store later — the cache shape
 * is in place so those can land without a rewrite.
 *
 * Fails soft: on error (e.g. no backend in dev) the investor's entry stays
 * undefined so callers fall back to placeholder data.
 */
export const useIntegrityStore = defineStore('integrity', () => {
  const byInvestor = ref<Record<number, IntegrityRow[]>>({})
  const loading = ref(false)
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
      byInvestor.value[investorId] = data.map((r) => ({
        securityId: r.security.id,
        name: r.security.name ?? r.security.isin,
        isin: r.security.isin,
        status: toIntegrityStatus(r.status),
        taxSafe: r.tax_safe,
        unitsFromHoldings: r.units_from_holdings,
        unitsFromTransactions: r.units_from_transactions,
        lastReconciledAt: r.last_reconciled_at,
      }))
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
    } finally {
      loading.value = false
    }
  }

  function clear(): void {
    byInvestor.value = {}
  }

  return { byInvestor, loading, error, rowsFor, rollupFor, load, clear }
})
