import { computed, ref, watch, type Ref } from 'vue'
import { api, type Schemas } from '@/api/client'
import type { AllocationSlice } from '@/components/charts/AllocationDonut.vue'
import type { ValuePoint } from '@/components/charts/PortfolioValueChart.vue'
import { useIntegrity } from '@/composables/useIntegrity'
import { toIntegrityStatus, type IntegrityStatus } from '@/integrity/status'
import { formatDate } from '@/utils/format'

export interface HoldingRow {
  securityId: number
  name: string
  assetClass: string
  value: number
  units: number
  integrity: IntegrityStatus
}

export interface DashboardSummary {
  netWorth: number
  invested: number
  totalReturnAmount: number
  totalReturnPercent: number
  xirr: number | null
  asOf: string
  allocation: AllocationSlice[]
  valueSeries: ValuePoint[]
  topHoldings: HoldingRow[]
}

export type RangeKey = '6M' | '1Y' | 'All'

// Each range maps to a value-series window + sampling granularity. "All" reaches
// back far enough to cover any real portfolio; the leading all-zero points
// (before the first holding) are trimmed below.
const RANGES: Record<RangeKey, { from: () => string; granularity: 'daily' | 'weekly' | 'monthly' }> = {
  '6M': { from: () => monthsAgo(6), granularity: 'monthly' },
  '1Y': { from: () => monthsAgo(12), granularity: 'monthly' },
  All: { from: () => '2000-01-01', granularity: 'monthly' },
}

// Display label + a fixed colour per security type, so donut slices stay
// semantic. (MF is the common case; the rest get distinct asset-class colours.)
const ASSET_META: Record<string, { label: string; color: string }> = {
  mf: { label: 'Mutual funds', color: 'var(--fm-asset-equity)' },
  equity: { label: 'Stocks', color: 'var(--fm-asset-intl)' },
  etf: { label: 'ETFs', color: 'var(--fm-asset-gold)' },
  bond: { label: 'Bonds', color: 'var(--fm-asset-debt)' },
  fd: { label: 'Fixed deposits', color: 'var(--fm-asset-cash)' },
  crypto: { label: 'Crypto', color: 'var(--fm-asset-crypto)' },
  foreign_equity: { label: 'International', color: 'var(--fm-asset-realestate)' },
}
function assetLabel(securityType: string): string {
  return ASSET_META[securityType]?.label ?? securityType
}

function monthsAgo(n: number): string {
  const d = new Date()
  d.setMonth(d.getMonth() - n)
  return d.toISOString().slice(0, 10)
}

function num(v: string | number | null | undefined): number {
  const n = typeof v === 'string' ? Number(v) : (v ?? 0)
  return Number.isFinite(n) ? n : 0
}

const EMPTY: DashboardSummary = {
  netWorth: 0,
  invested: 0,
  totalReturnAmount: 0,
  totalReturnPercent: 0,
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

    return {
      netWorth,
      invested,
      totalReturnAmount,
      totalReturnPercent,
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
        integrity: integrityBySecurity.value.get(h.security_id) ?? toIntegrityStatus(''),
      })),
    }
  })

  return { summary, rollup: integrity.rollup, loading, range, setRange, reload: loadAll }
}
