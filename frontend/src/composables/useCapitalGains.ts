import { computed, ref, type Ref } from 'vue'
import { api, type Schemas } from '@/api/client'
import { useIntegrityStore, type IntegrityRow } from '@/stores/integrity'
import { currentFy, fyOptions } from '@/utils/fy'

export type CapitalGains = Schemas['CapitalGainsOut']
export type CapitalGainRow = Schemas['CapitalGainRow']
export type Schedule112A = Schemas['Schedule112AResponse']

/**
 * Realised capital gains for one investor + FY: the STCG/LTCG view (a read) plus
 * the Schedule 112A worksheet (LTCG, for the CSV download). Both are built from
 * the same tax-ready disposals. Equity-MF only in v1. Also surfaces the holdings
 * left out (snapshot / mismatch / acknowledged) so the page can explain the gaps.
 *
 * Fails soft: on error `gains` stays null and `error` carries the message.
 */
export function useCapitalGains(investorId: Ref<number>) {
  const fy = ref(currentFy())
  const includeUnreconciled = ref(false)
  const gains = ref<CapitalGains | null>(null)
  const report = ref<Schedule112A | null>(null) // 112A worksheet, for the CSV download
  const loading = ref(false)
  const error = ref<string | null>(null)
  const built = ref(false)
  const builtAt = ref<Date | null>(null) // when the figures were last computed (freshness)
  const integrity = useIntegrityStore()

  async function build(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const [cg, worksheet] = await Promise.all([
        api.GET('/api/investors/{investor_id}/exports/capital-gains', {
          params: {
            path: { investor_id: investorId.value },
            query: { fy: fy.value, include_unreconciled: includeUnreconciled.value },
          },
        }),
        api.POST('/api/investors/{investor_id}/exports/schedule-112a', {
          params: { path: { investor_id: investorId.value } },
          body: { fy: fy.value, include_unreconciled: includeUnreconciled.value },
        }),
      ])
      if (cg.error || !cg.data) throw new Error('capital-gains request failed')
      gains.value = cg.data
      // The 112A worksheet powers the CSV download only; treat it as best-effort.
      report.value = worksheet.error ? null : (worksheet.data ?? null)
      built.value = true
      builtAt.value = new Date()
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      gains.value = null
    } finally {
      loading.value = false
    }
    // Powers the "left out, and why" section; cached, so cheap after the dashboard.
    void integrity.load(investorId.value)
  }

  // Holdings that can't enter the worksheet: no full history (snapshot), an
  // unresolved unit mismatch, or a mismatch the user acknowledged.
  const excluded = computed<IntegrityRow[]>(() =>
    integrity.rowsFor(investorId.value).filter((r) => !r.taxSafe),
  )
  const worksheetRowCount = computed(() => report.value?.row_count ?? 0)

  return {
    fy,
    fyOptions: fyOptions(),
    includeUnreconciled,
    gains,
    report,
    loading,
    error,
    built,
    builtAt,
    worksheetRowCount,
    excluded,
    build,
  }
}
