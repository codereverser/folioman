import { computed, ref, type Ref } from 'vue'
import { api, type Schemas } from '@/api/client'
import { useIntegrityStore, type IntegrityRow } from '@/stores/integrity'
import { currentFy, fyOptions } from '@/utils/fy'

export type Schedule112A = Schemas['Schedule112AResponse']

/**
 * Capital-gains worksheet (Schedule 112A shape) for one investor, plus the list
 * of holdings left *out* of it (snapshot / mismatch / acknowledged) so the
 * preview can explain the gaps. The worksheet is a draft for the user's own
 * review — never framed as a finished, file-ready return.
 *
 * Fails soft: on error `report` stays null and `error` carries the message.
 */
export function useTaxExport(investorId: Ref<number>) {
  const fy = ref(currentFy())
  const includeUnreconciled = ref(false)
  const report = ref<Schedule112A | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const built = ref(false)
  const integrity = useIntegrityStore()

  async function build(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const { data, error: apiError } = await api.POST(
        '/api/investors/{investor_id}/exports/schedule-112a',
        {
          params: { path: { investor_id: investorId.value } },
          body: { fy: fy.value, include_unreconciled: includeUnreconciled.value },
        },
      )
      if (apiError || !data) throw new Error('worksheet build failed')
      report.value = data
      built.value = true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      report.value = null
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

  const rowCount = computed(() => report.value?.row_count ?? 0)

  return {
    fy,
    fyOptions: fyOptions(),
    includeUnreconciled,
    report,
    loading,
    error,
    built,
    rowCount,
    excluded,
    build,
  }
}
