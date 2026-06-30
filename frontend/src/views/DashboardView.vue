<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, toRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import IntegrityHealthCard from '@/components/IntegrityHealthCard.vue'
import DeltaChip from '@/components/DeltaChip.vue'
import AssetClassSummary from '@/views/dashboard/AssetClassSummary.vue'
import { useDashboard, type RangeKey } from '@/composables/useDashboard'
import { RANGES } from '@/utils/portfolio'
import { useCountUp } from '@/composables/useCountUp'
import { useRosterStore } from '@/stores/roster'
import { useUiStore } from '@/stores/ui'
import { formatInr, formatInrCompact } from '@/utils/format'

const AllocationDonut = defineAsyncComponent(
  () => import('@/components/charts/AllocationDonut.vue'),
)
const PortfolioValueChart = defineAsyncComponent(
  () => import('@/components/charts/PortfolioValueChart.vue'),
)
const SelectButton = defineAsyncComponent(() => import('primevue/selectbutton'))

const route = useRoute()
const roster = useRosterStore()
const ui = useUiStore()
const loadCharts = ref(false)
const chartRegion = ref<HTMLElement | null>(null)
let stopChartObserver: () => void = () => {}

onMounted(() => {
  const target = chartRegion.value
  if (!target || typeof IntersectionObserver === 'undefined') {
    loadCharts.value = true
    return
  }
  const observer = new IntersectionObserver(
    (entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return
      loadCharts.value = true
      observer.disconnect()
      stopChartObserver = () => {}
    },
    { threshold: 0.2 },
  )
  observer.observe(target)
  stopChartObserver = () => observer.disconnect()
})
onBeforeUnmount(() => {
  stopChartObserver()
})

const investorId = computed(() => {
  const raw = route.params.investorId
  const n = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(n) ? n : (ui.selectedInvestorId ?? 0)
})
const investorName = computed(() => roster.investorName(investorId.value) ?? 'Investor')

// Live summary + the full net-worth series (fetched once); the range toggle just
// windows it client-side via `valueWindow`, and the chart's slider can free-zoom.
const { summary, rollup, range, setRange, valueWindow, valuationReady, loading } =
  useDashboard(investorId)

// Axis tick density follows the active range's sampling (see RANGES).
const valueGranularity = computed(() => RANGES[range.value].granularity)

// When prices are stale on open, the launch catch-up is already refreshing them in
// the background — tell the user once so the wait makes sense (values update on the
// next tick). Guarded in the store against re-toasting across investor switches.
watch(
  () => summary.value?.navsStale,
  (stale) => stale && ui.notifyNavRefreshOnce(),
  { immediate: true },
)
const integrityTo = computed(() => ({
  name: 'integrity',
  params: { investorId: investorId.value },
}))

const ranges: { label: string; value: RangeKey }[] = [
  { label: '1M', value: '1M' },
  { label: '3M', value: '3M' },
  { label: '6M', value: '6M' },
  { label: '1Y', value: '1Y' },
  { label: '3Y', value: '3Y' },
  { label: '5Y', value: '5Y' },
  { label: 'All', value: 'All' },
]

// Hero net worth counts up. XIRR is the money-weighted annualized headline (no CAGR:
// it assumes a single lump sum, wrong for a multi-cashflow SIP portfolio).
const heroNetWorth = useCountUp(toRef(() => summary.value.netWorth))

// Period change shown under the number, tied to the selected chart range — the
// value gained/lost from the window's start to now (price move, not cash flow).
const RANGE_LABEL: Record<RangeKey, string> = {
  '1M': 'past month',
  '3M': 'past 3 months',
  '6M': 'past 6 months',
  '1Y': 'past year',
  '3Y': 'past 3 years',
  '5Y': 'past 5 years',
  All: 'all time',
}
const rangeLabel = computed(() => RANGE_LABEL[range.value])
const periodReturn = computed<{ amount: number; pct: number } | null>(() => {
  const pts = summary.value.valueSeries
  if (pts.length < 2) return null
  const from = valueWindow.value?.from
  const inWindow = from ? pts.filter((p) => p.date >= from) : pts
  // First point with real value in the window (skip leading all-zero pre-holding days).
  const startPt = inWindow.find((p) => p.current > 0) ?? inWindow[0]
  const start = startPt?.current ?? 0
  const end = pts[pts.length - 1].current
  if (!start) return null
  return { amount: end - start, pct: ((end - start) / start) * 100 }
})

// Allocation: defaults to Asset class (the multi-asset overview); the toggle drills
// into Equity/Debt. AMC concentration is a fund lens deferred to the Insights view.
type AllocationGroup = 'asset' | 'category'
const allocationGroups: { label: string; value: AllocationGroup }[] = [
  { label: 'Asset class', value: 'asset' },
  { label: 'Equity/Debt', value: 'category' },
]
const allocationGroup = ref<AllocationGroup>('asset')
const allocationData = computed(() =>
  allocationGroup.value === 'asset' ? summary.value.allocation : summary.value.allocationByCategory,
)
</script>

<template>
  <section class="dashboard" :class="{ 'is-loading': loading }">
    <header class="page-head">
      <div>
        <h1>Dashboard</h1>
        <p class="sub">
          {{ investorName }} ·
          <RouterLink
            v-if="summary.navsStale"
            class="stale-navs"
            :to="{ name: 'settings', params: { tab: 'navs' } }"
            title="Prices haven't refreshed recently — see per-security freshness"
          >
            <i class="pi pi-exclamation-triangle" aria-hidden="true" /> NAVs as of
            {{ summary.navsAsOf }}
          </RouterLink>
          <template v-else>{{ summary.asOf }}</template>
        </p>
      </div>
    </header>

    <!-- Hero: net worth + the value-over-time chart as the main card. -->
    <article ref="chartRegion" class="card hero">
      <div class="hero-head">
        <div class="hero-net">
          <p class="eyebrow">Net worth</p>
          <p class="hero-value">{{ formatInrCompact(heroNetWorth) }}</p>
          <p class="hero-exact">{{ formatInr(summary.netWorth) }}</p>
          <p v-if="periodReturn" class="hero-period">
            <DeltaChip
              :amount="periodReturn.amount"
              :percent="periodReturn.pct"
              :value="periodReturn.amount"
              size="sm"
              compact
            />
            <span class="period-label">{{ rangeLabel }}</span>
          </p>
          <p class="hero-invested">Invested {{ formatInrCompact(summary.invested) }}</p>
        </div>
        <SelectButton
          v-if="loadCharts && summary.valueSeries?.length"
          class="hero-range"
          :model-value="range"
          :options="ranges"
          option-label="label"
          option-value="value"
          :allow-empty="false"
          size="small"
          @update:model-value="(v: RangeKey | null) => v && setRange(v)"
        />
        <span v-else class="range-placeholder" aria-hidden="true" />
      </div>

      <!-- A series exists → show the chart even while the latest day or two are still
           revaluing (it fills in when the worker finishes); just flag it's catching up. -->
      <template v-if="loadCharts && summary.valueSeries?.length">
        <PortfolioValueChart
          :data="summary.valueSeries"
          :granularity="valueGranularity"
          :window="valueWindow"
        />
        <p v-if="!valuationReady" class="chart-progress">
          Catching up the latest days — showing through your last computed day.
          <RouterLink class="navs-link" :to="{ name: 'settings', params: { tab: 'navs' } }"
            >Check NAV freshness →</RouterLink
          >
        </p>
      </template>
      <!-- No series yet, still computing → progress placeholder. -->
      <template v-else-if="!valuationReady">
        <div class="chart-placeholder value-placeholder" aria-hidden="true" />
        <p class="chart-progress">
          Portfolio valuation in progress — refresh in a bit. Showing values as of your latest
          statement meanwhile.
          <RouterLink class="navs-link" :to="{ name: 'settings', params: { tab: 'navs' } }"
            >Check NAV freshness →</RouterLink
          >
        </p>
      </template>
      <!-- Ready, but no day-wise history (snapshot-only holdings). -->
      <p v-else-if="loadCharts" class="chart-progress">
        No day-wise history yet — snapshot holdings (a demat eCAS) count toward net worth but not
        the trend. Import a transaction statement (a CAS or a broker tradebook) to build the
        history.
      </p>
      <div v-else class="chart-placeholder value-placeholder" aria-hidden="true" />
    </article>

    <!-- Headline metrics strip. -->
    <section class="card stat-strip">
      <div class="stat">
        <span class="eyebrow">All-time return</span>
        <DeltaChip
          :amount="summary.totalReturnAmount"
          :percent="summary.totalReturnPercent ?? undefined"
          :value="summary.totalReturnAmount"
          size="md"
          compact
        />
      </div>
      <div class="stat">
        <span class="eyebrow">XIRR</span>
        <DeltaChip
          v-if="summary.xirr !== null"
          :percent="summary.xirr"
          :value="summary.xirr"
          size="md"
        />
        <span v-else class="kpi-na">Needs more history</span>
      </div>
      <div class="stat">
        <span class="eyebrow">1D return</span>
        <DeltaChip
          v-if="summary.dayChangeAmount !== null"
          :amount="summary.dayChangeAmount"
          :percent="summary.dayChangePercent ?? undefined"
          size="sm"
          compact
        />
        <span v-else class="muted">—</span>
      </div>
      <div class="stat">
        <span class="eyebrow">Holdings</span>
        <span class="kpi-val">{{ summary.holdingsCount }}</span>
      </div>
    </section>

    <!-- Allocation + data integrity. -->
    <div class="bento">
      <article class="span-8 card chart-card">
        <div class="chart-head">
          <h2>Allocation</h2>
          <SelectButton
            v-if="loadCharts && allocationData.length > 0"
            :model-value="allocationGroup"
            :options="allocationGroups"
            option-label="label"
            option-value="value"
            :allow-empty="false"
            size="small"
            @update:model-value="(v: AllocationGroup | null) => v && (allocationGroup = v)"
          />
        </div>
        <AllocationDonut
          v-if="loadCharts"
          :data="allocationData"
          :center-label="formatInrCompact(summary.netWorth)"
        />
        <div v-else class="chart-placeholder donut-placeholder" aria-hidden="true" />
      </article>

      <IntegrityHealthCard class="span-4" :rollup="rollup" :review-to="integrityTo" />
    </div>

    <!-- Holdings, by asset class — open one to drill into its securities. -->
    <AssetClassSummary :holdings="summary.holdings" :investor-id="investorId" />
  </section>
</template>

<style scoped>
.dashboard {
  padding: var(--fm-space-6);
  max-width: var(--fm-content-max);
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-5);
}

.page-head {
  margin-bottom: 0;
}
.page-head h1 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
}
.page-head .sub {
  margin: 0.15rem 0 0;
  color: var(--fm-text-muted);
}
.page-head .sub .stale-navs {
  color: var(--p-amber-600, #d97706);
  font-weight: 600;
  text-decoration: none;
}
.page-head .sub .stale-navs:hover {
  text-decoration: underline;
}
.page-head .sub .stale-navs .pi {
  font-size: 0.75rem;
}

.bento {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: var(--fm-space-5);
}
.span-4 {
  grid-column: span 4;
}
.span-8 {
  grid-column: span 8;
}
.bento > * {
  min-width: 0;
}

.card {
  padding: var(--fm-space-5);
  background: var(--fm-surface);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
  box-shadow: var(--fm-shadow-sm);
}
.card h2 {
  margin: 0 0 var(--fm-space-3);
  font-size: 1rem;
  font-weight: 600;
}

.eyebrow {
  margin: 0;
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--fm-text-muted);
}

/* ---- hero (chart is the main card) ---- */
.hero-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--fm-space-3);
  flex-wrap: wrap;
  margin-bottom: var(--fm-space-3);
}
.hero-net {
  min-width: 0;
}
.hero-value {
  margin: 0.25rem 0 0;
  font-size: 2.6rem;
  line-height: 1.04;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
}
.hero-exact {
  margin: 0.25rem 0 0;
  font-size: 0.8125rem;
  color: var(--fm-text-subtle);
  font-variant-numeric: tabular-nums;
}
.hero-period {
  margin: 0.5rem 0 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.period-label {
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.hero-invested {
  margin: 0.35rem 0 0;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.hero-range {
  flex-shrink: 0;
}

/* ---- stat strip ---- */
.stat-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--fm-space-4);
  padding: var(--fm-space-4) var(--fm-space-5);
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  align-items: flex-start;
  padding-left: var(--fm-space-4);
  border-left: 1px solid var(--fm-border-subtle);
}
.stat:first-child {
  padding-left: 0;
  border-left: none;
}
.kpi-val {
  font-size: 1.25rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.kpi-na {
  font-size: 0.6875rem;
  color: var(--fm-text-subtle);
}

/* ---- charts ---- */
.chart-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--fm-space-2) var(--fm-space-3);
  flex-wrap: wrap;
  margin-bottom: var(--fm-space-2);
}
.chart-head h2 {
  margin: 0;
}
.chart-head :deep(.p-selectbutton),
.hero-range:deep(.p-selectbutton) {
  flex-shrink: 0;
}
.chart-head :deep(.p-togglebutton),
.chart-head :deep(.p-togglebutton-label) {
  white-space: nowrap;
}
.range-placeholder {
  display: block;
  width: 8.5rem;
  height: 2rem;
  border-radius: var(--fm-radius-sm);
  background: var(--fm-surface-raised);
}
.chart-placeholder {
  width: 100%;
  border-radius: var(--fm-radius-sm);
  background:
    linear-gradient(
      90deg,
      transparent 0,
      color-mix(in srgb, var(--fm-border-subtle) 32%, transparent) 50%,
      transparent 100%
    ),
    var(--fm-surface-raised);
}
.donut-placeholder {
  height: 260px;
}
.value-placeholder {
  height: 320px;
}
.chart-progress {
  margin: var(--fm-space-2) 0 0;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.chart-progress .navs-link {
  color: var(--p-primary-color);
  font-weight: 600;
  text-decoration: none;
  white-space: nowrap;
}
.chart-progress .navs-link:hover {
  text-decoration: underline;
}
.muted {
  color: var(--fm-text-muted);
}

/* While data loads (initial / investor switch), blur the figures so the
   momentary zeros read as "loading", not as real values. */
.dashboard.is-loading .hero-net,
.dashboard.is-loading .stat-strip,
.dashboard.is-loading .asset-summary .rows {
  filter: blur(7px);
  pointer-events: none;
  user-select: none;
  animation: data-pulse 1.1s ease-in-out infinite;
}
@keyframes data-pulse {
  0%,
  100% {
    opacity: 0.5;
  }
  50% {
    opacity: 0.72;
  }
}
@media (prefers-reduced-motion: reduce) {
  .dashboard.is-loading .hero-net,
  .dashboard.is-loading .stat-strip,
  .dashboard.is-loading .asset-summary .rows {
    animation: none;
    opacity: 0.55;
  }
}

/* ---- responsive ---- */
@media (max-width: 1024px) {
  .span-4,
  .span-8 {
    grid-column: span 12;
  }
}
@media (max-width: 640px) {
  .dashboard {
    padding: var(--fm-space-4);
    gap: var(--fm-space-4);
  }
  .card {
    padding: var(--fm-space-4);
  }
  .stat-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: var(--fm-space-4) var(--fm-space-3);
  }
  .stat:nth-child(odd) {
    padding-left: 0;
    border-left: none;
  }
  .hero-value {
    font-size: 2.2rem;
  }
}
</style>
