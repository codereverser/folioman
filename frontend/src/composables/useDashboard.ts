import { computed, ref, watch, type Ref } from 'vue'
import { api, type Schemas } from '@/api/client'
import type { AllocationSlice } from '@/components/charts/AllocationDonut.vue'
import type { ValuePoint } from '@/components/charts/PortfolioValueChart.vue'
import { useIntegrity } from '@/composables/useIntegrity'
import { toIntegrityStatus, type IntegrityStatus } from '@/integrity/status'
import { formatDate } from '@/utils/format'
import { ASSET_META, RANGES, assetLabel, num, type RangeKey } from '@/utils/portfolio'

export type { RangeKey }

export interface HoldingRow {
  securityId: number
  name: string
  assetClass: string
  value: number
  units: number
  returnPct: number | null // percent; null when cost basis is unknown
  integrity: IntegrityStatus
}

export interface DashboardSummary {
  netWorth: number
  invested: number
  totalReturnAmount: number
  totalReturnPercent: number
  dayChangeAmount: number | null // intraday INR change; null without 2 NAV points
  dayChangePercent: number | null
  xirr: number | null
  asOf: string
  allocation: AllocationSlice[]
  valueSeries: ValuePoint[]
  topHoldings: HoldingRow[]
}

const EMPTY: DashboardSummary = {
  netWorth: 0,
  invested: 0,
  totalReturnAmount: 0,
  totalReturnPercent: 0,
  dayChangeAmount: null,
  dayChangePercent: null,
  xirr: null,
  asOf: '—',
  allocation: [],
  valueSeries: [],
  topHoldings: [],
}

/**
 * Live per-investor dashboard data. Pulls the headline summary
 * (`GET /investors/{id}/summary`) and the net-worth series
 * (`GET /investors/{id}/value-series`); the range toggle re-fetches the series.
 * Per-holding integrity is joined from `useIntegrity`. Fails soft to zeros so the
 * shell still renders if a request errors (e.g. no backend in dev).
 */
export function useDashboard(investorId: Ref<number>) {
  const summaryData = ref<Schemas['InvestorSummaryOut'] | null>(null)
  const series = ref<Schemas['ValueSeriesPoint'][]>([])
  const range = ref<RangeKey>('1Y')
  const loading = ref(false)

  const integrity = useIntegrity(investorId)
  const integrityBySecurity = computed(() => {
    const map = new Map<number, IntegrityStatus>()
    for (const row of integrity.rows.value) map.set(row.securityId, row.status)
    return map
  })

  async function loadSummary(): Promise<void> {
    const { data } = await api.GET('/api/investors/{investor_id}/summary', {
      params: { path: { investor_id: investorId.value } },
    })
    summaryData.value = data ?? null
  }

  async function loadSeries(): Promise<void> {
    const cfg = RANGES[range.value]
    const { data } = await api.GET('/api/investors/{investor_id}/value-series', {
      params: {
        path: { investor_id: investorId.value },
        query: { from: cfg.from(), granularity: cfg.granularity },
      },
    })
    series.value = data?.points ?? []
  }

  async function loadAll(): Promise<void> {
    loading.value = true
    try {
      await Promise.all([loadSummary(), loadSeries()])
    } finally {
      loading.value = false
    }
  }

  function setRange(next: RangeKey): void {
    if (next === range.value) return
    range.value = next
    void loadSeries()
  }

  watch(investorId, () => void loadAll(), { immediate: true })

  // The net-worth line; trim the leading all-zero stretch before the first holding.
  const valueSeries = computed<ValuePoint[]>(() => {
    const points = series.value.map((p) => ({
      date: p.date,
      current: num(p.value_inr),
      invested: num(p.invested_inr),
    }))
    const firstReal = points.findIndex((p) => p.current !== 0 || p.invested !== 0)
    return firstReal > 0 ? points.slice(firstReal) : points
  })

  const summary = computed<DashboardSummary>(() => {
    const s = summaryData.value
    if (!s) return { ...EMPTY, valueSeries: valueSeries.value }

    const netWorth = num(s.total_inr)
    // Invested = FIFO cost basis of held units, from the latest series point.
    const invested = valueSeries.value.at(-1)?.invested ?? 0
    const totalReturnAmount = netWorth - invested
    const totalReturnPercent = invested > 0 ? (totalReturnAmount / invested) * 100 : 0

    // Portfolio day-change: the API gives the absolute INR move; derive the
    // percent against the prior value (net worth minus today's move).
    const dayChangeAmount = s.day_change_inr == null ? null : num(s.day_change_inr)
    const priorValue = dayChangeAmount == null ? null : netWorth - dayChangeAmount
    const dayChangePercent =
      dayChangeAmount != null && priorValue ? (dayChangeAmount / priorValue) * 100 : null

    return {
      netWorth,
      invested,
      totalReturnAmount,
      totalReturnPercent,
      dayChangeAmount,
      dayChangePercent,
      xirr: s.xirr == null ? null : s.xirr * 100, // fraction → percent for the card
      asOf: `as of ${formatDate(s.as_of)}`,
      allocation: (s.asset_mix ?? []).map<AllocationSlice>((row) => ({
        name: assetLabel(row.security_type),
        value: num(row.value_inr),
        color: ASSET_META[row.security_type]?.color,
      })),
      valueSeries: valueSeries.value,
      topHoldings: (s.top_holdings ?? []).map<HoldingRow>((h) => ({
        securityId: h.security_id,
        name: h.name,
        assetClass: assetLabel(h.security_type),
        value: num(h.value_inr),
        units: num(h.units),
        returnPct: h.return_pct == null ? null : h.return_pct * 100,
        integrity: integrityBySecurity.value.get(h.security_id) ?? toIntegrityStatus(''),
      })),
    }
  })

  return { summary, rollup: integrity.rollup, loading, range, setRange, reload: loadAll }
}
