<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, toRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import IntegrityHealthCard from '@/components/IntegrityHealthCard.vue'
import DeltaChip from '@/components/DeltaChip.vue'
import ReturnsStrip from '@/components/ReturnsStrip.vue'
import AssetClassSummary from '@/views/dashboard/AssetClassSummary.vue'
import { useDashboard, type RangeKey } from '@/composables/useDashboard'
import { RANGE_LABEL, RANGE_OPTIONS, windowChange } from '@/utils/portfolio'
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
const { summary, rollup, range, setRange, valueWindow, granularity, valuationReady, loading } =
  useDashboard(investorId)

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

// Hero net worth counts up. XIRR is the money-weighted annualized headline (no CAGR:
// it assumes a single lump sum, wrong for a multi-cashflow SIP portfolio).
const heroNetWorth = useCountUp(toRef(() => summary.value.netWorth))

// Period change shown under the number, tied to the selected chart range — the
// value gained/lost from the window's start to now (price move, not cash flow).
const rangeLabel = computed(() => RANGE_LABEL[range.value])
const periodReturn = computed(() =>
  windowChange(summary.value.valueSeries, valueWindow.value?.from),
)

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
  <section class="fm-page">
    <header class="fm-page-head">
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
    <article ref="chartRegion" class="fm-card">
      <div class="fm-hero-head">
        <div class="fm-hero-net" :class="{ 'fm-blur-loading': loading }">
          <p class="fm-eyebrow">Net worth</p>
          <p class="fm-hero-value">{{ formatInrCompact(heroNetWorth) }}</p>
          <p class="fm-hero-exact">{{ formatInr(summary.netWorth) }}</p>
          <p v-if="periodReturn" class="fm-hero-period">
            <DeltaChip
              :amount="periodReturn.amount"
              :percent="periodReturn.pct"
              :value="periodReturn.amount"
              size="sm"
              compact
            />
            <span class="fm-period-label">{{ rangeLabel }}</span>
          </p>
          <p class="fm-hero-invested">Invested {{ formatInrCompact(summary.invested) }}</p>
        </div>
        <SelectButton
          v-if="loadCharts && summary.valueSeries?.length"
          class="fm-hero-range"
          :model-value="range"
          :options="RANGE_OPTIONS"
          option-label="label"
          option-value="value"
          :allow-empty="false"
          size="small"
          @update:model-value="(v: RangeKey | null) => v && setRange(v)"
        />
        <span v-else class="fm-range-placeholder" aria-hidden="true" />
      </div>

      <!-- A series exists → show the chart even while the latest day or two are still
           revaluing (it fills in when the worker finishes); just flag it's catching up. -->
      <template v-if="loadCharts && summary.valueSeries?.length">
        <PortfolioValueChart
          :data="summary.valueSeries"
          :granularity="granularity"
          :window="valueWindow"
        />
        <p v-if="!valuationReady" class="fm-chart-progress">
          Catching up the latest days — showing through your last computed day.
          <RouterLink :to="{ name: 'settings', params: { tab: 'navs' } }"
            >Check NAV freshness →</RouterLink
          >
        </p>
      </template>
      <!-- No series yet, still computing → progress placeholder. -->
      <template v-else-if="!valuationReady">
        <div class="fm-chart-placeholder fm-value-placeholder" aria-hidden="true" />
        <p class="fm-chart-progress">
          Portfolio valuation in progress — refresh in a bit. Showing values as of your latest
          statement meanwhile.
          <RouterLink :to="{ name: 'settings', params: { tab: 'navs' } }"
            >Check NAV freshness →</RouterLink
          >
        </p>
      </template>
      <!-- Ready, but no day-wise history (snapshot-only holdings). -->
      <p v-else-if="loadCharts" class="fm-chart-progress">
        No day-wise history yet — snapshot holdings (a demat eCAS) count toward net worth but not
        the trend. Import a transaction statement (a CAS or a broker tradebook) to build the
        history.
      </p>
      <div v-else class="fm-chart-placeholder fm-value-placeholder" aria-hidden="true" />
    </article>

    <!-- Headline metrics strip. -->
    <section class="fm-card fm-stat-strip" :class="{ 'fm-blur-loading': loading }">
      <div class="fm-stat">
        <span class="fm-eyebrow">All-time return</span>
        <DeltaChip
          :amount="summary.totalReturnAmount"
          :percent="summary.totalReturnPercent ?? undefined"
          :value="summary.totalReturnAmount"
          size="md"
          compact
        />
      </div>
      <div class="fm-stat">
        <span class="fm-eyebrow">XIRR</span>
        <DeltaChip
          v-if="summary.xirr !== null"
          :percent="summary.xirr"
          :value="summary.xirr"
          size="md"
        />
        <span v-else class="fm-kpi-na">Needs more history</span>
      </div>
      <div class="fm-stat">
        <span class="fm-eyebrow">1D return</span>
        <DeltaChip
          v-if="summary.dayChangeAmount !== null"
          :amount="summary.dayChangeAmount"
          :percent="summary.dayChangePercent ?? undefined"
          size="sm"
          compact
        />
        <span v-else class="muted">—</span>
      </div>
      <div class="fm-stat">
        <span class="fm-eyebrow">Holdings</span>
        <span class="fm-kpi-val">{{ summary.holdingsCount }}</span>
      </div>
    </section>

    <!-- Trailing returns: money-weighted (XIRR) over standard windows. -->
    <ReturnsStrip :returns="summary.periodReturns" />

    <!-- Allocation + data integrity. -->
    <div class="fm-bento">
      <article class="fm-span-8 fm-card">
        <div class="fm-chart-head">
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
        <div v-else class="fm-chart-placeholder fm-donut-placeholder" aria-hidden="true" />
      </article>

      <IntegrityHealthCard class="fm-span-4" :rollup="rollup" :review-to="integrityTo" />
    </div>

    <!-- Holdings, by asset class — open one to drill into its securities. -->
    <AssetClassSummary
      :class="{ 'fm-blur-loading': loading }"
      :holdings="summary.holdings"
      :investor-id="investorId"
    />
  </section>
</template>

<style scoped>
.fm-page-head .sub .stale-navs {
  color: var(--p-amber-600, #d97706);
  font-weight: 600;
  text-decoration: none;
}
.fm-page-head .sub .stale-navs:hover {
  text-decoration: underline;
}
.fm-page-head .sub .stale-navs .pi {
  font-size: 0.75rem;
}
.muted {
  color: var(--fm-text-muted);
}
</style>
