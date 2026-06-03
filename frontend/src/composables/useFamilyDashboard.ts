import { computed, getCurrentScope, onScopeDispose, ref, watch, type Ref } from 'vue'
import { api, type Schemas } from '@/api/client'
import type { AllocationSlice } from '@/components/charts/AllocationDonut.vue'
import type { ValuePoint } from '@/components/charts/PortfolioValueChart.vue'
import { useRosterStore } from '@/stores/roster'
import { useUiStore } from '@/stores/ui'

const POLL_MS = 5000
const POLL_MAX_TICKS = 120 // ~10 min cap
import { formatDate } from '@/utils/format'
import { ASSET_META, RANGES, assetLabel, num, type RangeKey } from '@/utils/portfolio'

export type { RangeKey }

export interface FamilyHoldingRow {
  securityId: number
  name: string
  assetClass: string
  value: number
  units: number
  returnPct: number | null
}

export interface FamilyMember {
  id: number
  name: string
  totalInr: number
}

export interface FamilySummary {
  total: number
  investorCount: number
  folioCount: number
  dayChangeAmount: number | null
  dayChangePercent: number | null
  xirr: number | null
  asOf: string
  allocation: AllocationSlice[]
  valueSeries: ValuePoint[]
  topHoldings: FamilyHoldingRow[]
}

const EMPTY: FamilySummary = {
  total: 0,
  investorCount: 0,
  folioCount: 0,
  dayChangeAmount: null,
  dayChangePercent: null,
  xirr: null,
  asOf: '—',
  allocation: [],
  valueSeries: [],
  topHoldings: [],
}

/**
 * Live combined-portfolio data for the family page: the server-side aggregate
 * (`/families/{id}/aggregate`), the family net-worth series
 * (`/families/{id}/value-series`, range-toggled), and a per-member breakdown
 * (each investor's total, for the drill-in cards). Fails soft to zeros.
 */
export function useFamilyDashboard(familyId: Ref<number>) {
  const aggregate = ref<Schemas['FamilyAggregateOut'] | null>(null)
  const series = ref<Schemas['ValueSeriesPoint'][]>([])
  const members = ref<FamilyMember[]>([])
  const range = ref<RangeKey>('1Y')
  const loading = ref(false)
  const valuationStatus = ref<string>('ready')
  const valuationReady = computed(() => valuationStatus.value === 'ready')
  const roster = useRosterStore()
  const ui = useUiStore()
  let pollTimer: ReturnType<typeof setInterval> | null = null

  async function loadAggregate(): Promise<void> {
    const { data } = await api.GET('/api/families/{family_id}/aggregate', {
      params: { path: { family_id: familyId.value } },
    })
    aggregate.value = data ?? null
  }

  async function loadSeries(): Promise<void> {
    const cfg = RANGES[range.value]
    const { data } = await api.GET('/api/families/{family_id}/value-series', {
      params: {
        path: { family_id: familyId.value },
        query: { from: cfg.from(), granularity: cfg.granularity },
      },
    })
    series.value = data?.points ?? []
  }

  // Members come from the cached roster; each member's total is its own summary.
  async function loadMembers(): Promise<void> {
    await roster.ensureLoaded()
    const list = roster.investors.filter((i) => i.familyId === familyId.value)
    members.value = await Promise.all(
      list.map(async (m) => {
        const { data } = await api.GET('/api/investors/{investor_id}/summary', {
          params: { path: { investor_id: m.id } },
        })
        return { id: m.id, name: m.name, totalInr: data ? num(data.total_inr) : 0 }
      }),
    )
  }

  async function loadStatus(): Promise<void> {
    const { data } = await api.GET('/api/families/{family_id}/valuation-status', {
      params: { path: { family_id: familyId.value } },
    })
    valuationStatus.value = data?.status ?? 'ready'
  }

  function stopPolling(): void {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  function startPolling(): void {
    stopPolling()
    let ticks = 0
    pollTimer = setInterval(async () => {
      ticks += 1
      await loadStatus()
      if (valuationReady.value) {
        stopPolling()
        await Promise.all([loadAggregate(), loadSeries()])
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
      await Promise.all([loadAggregate(), loadSeries(), loadMembers(), loadStatus()])
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

  watch(familyId, () => void loadAll(), { immediate: true })
  if (getCurrentScope()) onScopeDispose(stopPolling)

  const valueSeries = computed<ValuePoint[]>(() => {
    const points = series.value.map((p) => ({
      date: p.date,
      current: num(p.value_inr),
      invested: num(p.invested_inr),
    }))
    const firstReal = points.findIndex((p) => p.current !== 0 || p.invested !== 0)
    return firstReal > 0 ? points.slice(firstReal) : points
  })

  const summary = computed<FamilySummary>(() => {
    const a = aggregate.value
    if (!a) return { ...EMPTY, valueSeries: valueSeries.value }

    const total = num(a.total_inr)
    const dayChangeAmount = a.day_change_inr == null ? null : num(a.day_change_inr)
    const priorValue = dayChangeAmount == null ? null : total - dayChangeAmount
    const dayChangePercent =
      dayChangeAmount != null && priorValue ? (dayChangeAmount / priorValue) * 100 : null

    return {
      total,
      investorCount: a.investor_count,
      folioCount: a.folio_count ?? 0,
      dayChangeAmount,
      dayChangePercent,
      xirr: a.xirr == null ? null : a.xirr * 100,
      asOf: `as of ${formatDate(a.as_of)}`,
      allocation: (a.asset_mix ?? []).map<AllocationSlice>((row) => ({
        name: assetLabel(row.security_type),
        value: num(row.value_inr),
        color: ASSET_META[row.security_type]?.color,
      })),
      valueSeries: valueSeries.value,
      topHoldings: (a.top_holdings ?? []).map<FamilyHoldingRow>((h) => ({
        securityId: h.security_id,
        name: h.name,
        assetClass: assetLabel(h.security_type),
        value: num(h.value_inr),
        units: num(h.units),
        returnPct: h.return_pct == null ? null : h.return_pct * 100,
      })),
    }
  })

  return {
    summary,
    members,
    loading,
    range,
    setRange,
    reload: loadAll,
    valuationReady,
    valuationStatus,
  }
}
