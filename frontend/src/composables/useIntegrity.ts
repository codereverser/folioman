import { computed, ref, watch, type Ref } from 'vue'
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
 * Live integrity status for an investor, from /investors/{id}/integrity.
 * Fails soft: on error (e.g. no backend in dev) `loaded` stays false so callers
 * can fall back to placeholder data.
 */
export function useIntegrity(investorId: Ref<number>) {
  const rows = ref<IntegrityRow[]>([])
  const loading = ref(false)
  const loaded = ref(false)
  const error = ref<string | null>(null)

  async function load(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const { data, error: apiError } = await api.GET('/api/investors/{investor_id}/integrity', {
        params: { path: { investor_id: investorId.value } },
      })
      if (apiError || !data) throw new Error('integrity request failed')
      rows.value = data.map((r) => ({
        securityId: r.security.id,
        name: r.security.name ?? r.security.isin,
        isin: r.security.isin,
        status: toIntegrityStatus(r.status),
        taxSafe: r.tax_safe,
        unitsFromHoldings: r.units_from_holdings,
        unitsFromTransactions: r.units_from_transactions,
        lastReconciledAt: r.last_reconciled_at,
      }))
      loaded.value = true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      loaded.value = false
    } finally {
      loading.value = false
    }
  }

  const rollup = computed<IntegrityRollup>(() => rollupIntegrity(rows.value.map((r) => r.status)))

  watch(investorId, () => void load(), { immediate: true })

  return { rows, rollup, loading, loaded, error, reload: load }
}
