import { computed, ref, watch, type Ref } from 'vue'
import { api, type Schemas } from '@/api/client'
import type { NavPoint } from '@/components/charts/NavHistoryChart.vue'
import type { ValuePoint } from '@/components/charts/PortfolioValueChart.vue'
import { toIntegrityStatus, type IntegrityStatus } from '@/integrity/status'

export type SchemeDetail = Schemas['SchemeDetailOut']

function num(v: string | number | null | undefined): number {
  const n = typeof v === 'string' ? Number(v) : (v ?? 0)
  return Number.isFinite(n) ? n : 0
}

/**
 * Per-scheme detail for `SchemeDetailView`: identity, current metrics, NAV
 * history, integrity, and the ledger — one call to
 * `GET /investors/{id}/holdings/{securityId}`. Fails soft: on error `detail`
 * stays null and `notFound` flags a 404 (e.g. a stale link to a sold scheme).
 */
export function useScheme(investorId: Ref<number>, securityId: Ref<number>) {
  const detail = ref<SchemeDetail | null>(null)
  const loading = ref(false)
  const notFound = ref(false)

  async function load(): Promise<void> {
    loading.value = true
    notFound.value = false
    try {
      const { data, error } = await api.GET('/api/investors/{investor_id}/holdings/{security_id}', {
        params: { path: { investor_id: investorId.value, security_id: securityId.value } },
      })
      if (error || !data) {
        notFound.value = true
        detail.value = null
        return
      }
      detail.value = data
    } finally {
      loading.value = false
    }
  }

  // Value-over-time (units held × NAV) — its own endpoint, fetched lazily the first
  // time the chart is switched to "Value", and reset when the scheme changes.
  const valueSeries = ref<ValuePoint[]>([])
  const valueLoaded = ref(false)
  async function loadValueSeries(): Promise<void> {
    if (valueLoaded.value) return
    valueLoaded.value = true
    const { data } = await api.GET(
      '/api/investors/{investor_id}/holdings/{security_id}/value-series',
      {
        params: {
          path: { investor_id: investorId.value, security_id: securityId.value },
          query: { granularity: 'monthly' },
        },
      },
    )
    if (data)
      valueSeries.value = data.points.map((p) => ({
        date: p.date,
        current: num(p.value_inr),
        invested: num(p.invested_inr),
      }))
  }

  watch(
    [investorId, securityId],
    () => {
      valueSeries.value = []
      valueLoaded.value = false
      void load()
    },
    { immediate: true },
  )

  // NAV history mapped to the chart's point shape.
  const navSeries = computed<NavPoint[]>(() =>
    (detail.value?.nav_history ?? []).map((p) => ({ date: p.date, nav: num(p.nav) })),
  )

  // Worst integrity status across this security's folios drives the header badge.
  const SEVERITY: Record<IntegrityStatus, number> = {
    mismatch: 4,
    snapshot_only: 3,
    user_acknowledged: 2,
    reconciled: 1,
    full_history: 1,
    unknown: 0,
  }
  const integrityStatus = computed<IntegrityStatus>(() => {
    const rows = detail.value?.integrity ?? []
    if (!rows.length) return toIntegrityStatus('')
    return rows
      .map((r) => toIntegrityStatus(r.status))
      .reduce((worst, s) => (SEVERITY[s] > SEVERITY[worst] ? s : worst))
  })

  return {
    detail,
    loading,
    notFound,
    navSeries,
    valueSeries,
    loadValueSeries,
    integrityStatus,
    reload: load,
  }
}
