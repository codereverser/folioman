import { computed, getCurrentScope, onScopeDispose, ref, watch, type Ref } from 'vue'
import { api, type Schemas } from '@/api/client'
import type { AllocationSlice } from '@/components/charts/AllocationDonut.vue'
import type { ValuePoint } from '@/components/charts/PortfolioValueChart.vue'
import { toIntegrityStatus, type IntegrityStatus } from '@/integrity/status'
import { useIntegrityStore } from '@/stores/integrity'
import { useUiStore } from '@/stores/ui'
import { formatDate } from '@/utils/format'

const POLL_MS = 5000
const POLL_MAX_TICKS = 120 // ~10 min cap
import {
  ASSET_META,
  RANGES,
  assetLabel,
  categoryColor,
  num,
  rampColor,
  shortAmc,
  type RangeKey,
} from '@/utils/portfolio'

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

// A fund on the MF breakdown page: a holding plus its grouping keys, XIRR, and
// absolute ₹ contribution (for the "contribution to returns" strip).
export interface FundRow extends HoldingRow {
  amc: string
  category: string
  xirr: number | null // percent; null when not computable
  gain: number | null // value − invested, in ₹; null when cost basis is unknown
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
  // total_inr is a last-known value (statement close / last computed day), not a
  // live valuation at as_of — e.g. NAVs not fetched yet. as_of is that value's date.
  isProvisional: boolean
  // The prices backing the total are old (the feed hasn't run for >1 trading day).
  // navsAsOf is the freshest NAV date, formatted for the "NAVs as of …" subtitle.
  navsStale: boolean
  navsAsOf: string
  allocation: AllocationSlice[] // by asset class (the "All" view; MF-only for now)
  allocationByCategory: AllocationSlice[] // equity vs debt
  allocationByAmc: AllocationSlice[] // by fund house
  valueSeries: ValuePoint[]
  topHoldings: HoldingRow[]
  funds: FundRow[] // priced mutual funds only, for the MF breakdown's grouped list
  // MF-only allocation for the "Mutual funds" tab (excludes stocks/other assets,
  // which we don't support yet — they stay in net worth + the All→Asset class view).
  mfByCategory: AllocationSlice[]
  mfByAmc: AllocationSlice[]
  mfTotal: number
  holdingsCount: number // priced holdings tracked (hero KPI)
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
  isProvisional: false,
  navsStale: false,
  navsAsOf: '',
  allocation: [],
  allocationByCategory: [],
  allocationByAmc: [],
  valueSeries: [],
  topHoldings: [],
  funds: [],
  mfByCategory: [],
  mfByAmc: [],
  mfTotal: 0,
  holdingsCount: 0,
}

// Map a backend allocation breakdown into donut slices. With `cap`, keep the
// largest `cap` buckets and fold the remainder into a neutral "Others" slice so
// a long tail (many AMCs) doesn't overflow the legend.
function toSlices(
  rows: { label: string; value_inr: string }[],
  color: (label: string, index: number) => string,
  cap?: number,
  label: (raw: string) => string = (raw) => raw,
): AllocationSlice[] {
  const head = cap ? rows.slice(0, cap) : rows
  const slices = head.map<AllocationSlice>((r, i) => ({
    name: label(r.label),
    value: num(r.value_inr),
    color: color(r.label, i),
  }))
  if (cap && rows.length > cap) {
    const rest = rows.slice(cap).reduce((sum, r) => sum + num(r.value_inr), 0)
    if (rest > 0) slices.push({ name: 'Others', value: rest, color: 'var(--fm-asset-cash)' })
  }
  return slices
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
  // Day-wise valuation readiness — gates the net-worth chart (the headline numbers
  // stay ungated, backed by the provisional value until the worker finishes).
  const valuationStatus = ref<string>('ready')
  const valuationReady = computed(() => valuationStatus.value === 'ready')
  const ui = useUiStore()
  let pollTimer: ReturnType<typeof setInterval> | null = null

  // Per-holding integrity is read from the shared integrity store, so an
  // acknowledge on the Integrity page reflects here without a refetch.
  const integrityStore = useIntegrityStore()
  const integrityBySecurity = computed(() => {
    const map = new Map<number, IntegrityStatus>()
    for (const row of integrityStore.rowsFor(investorId.value)) map.set(row.securityId, row.status)
    return map
  })
  const rollup = computed(() => integrityStore.rollupFor(investorId.value))

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

  async function loadStatus(): Promise<void> {
    const { data } = await api.GET('/api/investors/{investor_id}/valuation-status', {
      params: { path: { investor_id: investorId.value } },
    })
    valuationStatus.value = data?.status ?? 'ready'
  }

  function stopPolling(): void {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  // While valuation is computing, poll the status; when it flips to ready, reload
  // the (now precise) series + headline and toast the user.
  function startPolling(): void {
    stopPolling()
    let ticks = 0
    pollTimer = setInterval(async () => {
      ticks += 1
      await loadStatus()
      if (valuationReady.value) {
        stopPolling()
        await Promise.all([loadSummary(), loadSeries()])
        ui.notify({ severity: 'success', summary: 'Portfolio valuation ready' })
      } else if (ticks >= POLL_MAX_TICKS) {
        stopPolling()
      }
    }, POLL_MS)
  }

  async function loadAll(): Promise<void> {
    loading.value = true
    stopPolling()
    try {
      await Promise.all([
        loadSummary(),
        loadSeries(),
        loadStatus(),
        integrityStore.load(investorId.value),
      ])
    } finally {
      loading.value = false
    }
    if (!valuationReady.value) startPolling()
  }

  function setRange(next: RangeKey): void {
    if (next === range.value) return
    range.value = next
    void loadSeries()
  }

  watch(investorId, () => void loadAll(), { immediate: true })
  if (getCurrentScope()) onScopeDispose(stopPolling)

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

    // The "Mutual funds" tab is fund-only: stocks/demat holdings (unsupported yet)
    // shouldn't sit under a fund category. Derive its list + donut from MF holdings.
    const mfHoldings = (s.holdings ?? []).filter((h) => h.security_type === 'mf')
    const mfMix = (key: (h: (typeof mfHoldings)[number]) => string | null | undefined) => {
      const m = new Map<string, number>()
      for (const h of mfHoldings) {
        if (h.value_inr == null) continue
        m.set(key(h) || 'Other', (m.get(key(h) || 'Other') ?? 0) + num(h.value_inr))
      }
      return [...m.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([label, value]) => ({ label, value_inr: String(value) }))
    }

    return {
      netWorth,
      invested,
      totalReturnAmount,
      totalReturnPercent,
      dayChangeAmount,
      dayChangePercent,
      xirr: s.xirr == null ? null : s.xirr * 100, // fraction → percent for the card
      asOf: `as of ${formatDate(s.as_of)}${s.is_provisional ? ' · provisional' : ''}`,
      isProvisional: s.is_provisional,
      navsStale: s.navs_stale ?? false,
      navsAsOf: s.navs_as_of ? formatDate(s.navs_as_of) : '',
      allocation: (s.asset_mix ?? []).map<AllocationSlice>((row) => ({
        name: assetLabel(row.security_type),
        value: num(row.value_inr),
        color: ASSET_META[row.security_type]?.color,
      })),
      allocationByCategory: toSlices(s.category_mix ?? [], categoryColor),
      // Cap fund-house slices so the donut legend stays readable; the tail rolls
      // into a neutral "Others" slice (backend already orders buckets value-desc).
      allocationByAmc: toSlices(s.amc_mix ?? [], (_label, i) => rampColor(i), 6, shortAmc),
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
      funds: mfHoldings.map<FundRow>((h) => ({
        securityId: h.security_id,
        name: h.name,
        assetClass: assetLabel(h.security_type),
        amc: shortAmc(h.amc || 'Other'),
        category: h.category || 'Other',
        value: num(h.value_inr),
        units: num(h.units),
        returnPct: h.return_pct == null ? null : h.return_pct * 100,
        xirr: h.xirr == null ? null : h.xirr * 100,
        gain: h.invested_inr == null ? null : num(h.value_inr) - num(h.invested_inr),
        integrity: integrityBySecurity.value.get(h.security_id) ?? toIntegrityStatus(''),
      })),
      mfByCategory: toSlices(mfMix((h) => h.category), categoryColor),
      mfByAmc: toSlices(mfMix((h) => h.amc), (_label, i) => rampColor(i), 6, shortAmc),
      mfTotal: mfHoldings.reduce((sum, h) => sum + num(h.value_inr), 0),
      holdingsCount: s.holdings_count ?? (s.holdings?.length ?? 0),
    }
  })

  return {
    summary,
    rollup,
    loading,
    range,
    setRange,
    reload: loadAll,
    valuationReady,
    valuationStatus,
  }
}
