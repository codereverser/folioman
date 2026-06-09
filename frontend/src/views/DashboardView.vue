<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, toRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Popover from 'primevue/popover'
import IntegrityHealthCard from '@/components/IntegrityHealthCard.vue'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import DeltaChip from '@/components/DeltaChip.vue'
import DashboardFunds from '@/views/dashboard/DashboardFunds.vue'
import DashboardStocks from '@/views/dashboard/DashboardStocks.vue'
import { useDashboard, type RangeKey } from '@/composables/useDashboard'
import { useCountUp } from '@/composables/useCountUp'
import { useRosterStore } from '@/stores/roster'
import { useUiStore } from '@/stores/ui'
import { formatInr, formatInrCompact, formatUnits } from '@/utils/format'

const AllocationDonut = defineAsyncComponent(() => import('@/components/charts/AllocationDonut.vue'))
const PortfolioValueChart = defineAsyncComponent(() => import('@/components/charts/PortfolioValueChart.vue'))
const SelectButton = defineAsyncComponent(() => import('primevue/selectbutton'))

const route = useRoute()
const router = useRouter()
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

// Live summary + net-worth series; the range toggle re-fetches the series.
const { summary, rollup, range, setRange, valuationReady } = useDashboard(investorId)

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
  { label: '3M', value: '3M' },
  { label: '6M', value: '6M' },
  { label: '1Y', value: '1Y' },
  { label: '3Y', value: '3Y' },
  { label: '5Y', value: '5Y' },
  { label: 'All', value: 'All' },
]

// Hero: net worth counts up; the all-time return % switches between the simple
// absolute return and XIRR (money-weighted annualized, the headline default).
// (No CAGR: it assumes a single lump sum and a known holding period — wrong for a
// multi-cashflow SIP portfolio, where XIRR is the correct annualized figure.)
const heroNetWorth = useCountUp(toRef(() => summary.value.netWorth))

// Main-dashboard allocation: the *asset-allocation* question (asset class →
// equity/debt). Fund-house (AMC) concentration is a fund-level lens and lives on
// the Mutual funds tab, reachable via the "View fund breakdown" link. Defaults to
// Equity/Debt because the asset-class view is a single "Mutual funds" slice until
// multi-asset import lands.
type AllocationGroup = 'asset' | 'category'
const allocationGroups: { label: string; value: AllocationGroup }[] = [
  { label: 'Asset class', value: 'asset' },
  { label: 'Equity/Debt', value: 'category' },
]
const allocationGroup = ref<AllocationGroup>('category')
const allocationData = computed(() =>
  allocationGroup.value === 'asset' ? summary.value.allocation : summary.value.allocationByCategory,
)

// Asset-class tab from the route (deep-linkable): no segment = All, `/mf` = MF.
const activeTab = computed<'all' | 'mf' | 'stocks'>(() => {
  if (route.params.assetTab === 'mf') return 'mf'
  if (route.params.assetTab === 'stocks') return 'stocks'
  return 'all'
})

// "More" tab → a popover listing planned asset classes + a link to vote on what's
// next (a GitHub Discussions poll). Keeps the tab strip tidy and frames these as
// "planned · vote" rather than promising delivery.
const POLL_URL = 'https://github.com/codereverser/folioman/discussions/52'
const plannedAssets: { label: string; icon: string }[] = [
  { label: 'US stocks', icon: 'pi pi-globe' },
  { label: 'Gold', icon: 'pi pi-star' },
  { label: 'Crypto', icon: 'pi pi-bitcoin' },
  { label: 'Fixed deposits', icon: 'pi pi-wallet' },
  { label: 'Real estate', icon: 'pi pi-home' },
]
const moreOp = ref<InstanceType<typeof Popover>>()
function tabTo(asset?: 'mf' | 'stocks') {
  return {
    name: 'dashboard',
    params: { investorId: investorId.value, ...(asset ? { assetTab: asset } : {}) },
  }
}

// Contribution to returns: which funds added (or shed) the most rupees. Purely
// descriptive — no ranking-as-advice.
const topContributors = computed(() =>
  summary.value.funds
    .filter((f) => f.gain !== null && f.gain > 0)
    .sort((a, b) => (b.gain ?? 0) - (a.gain ?? 0))
    .slice(0, 5),
)
const detractors = computed(() =>
  summary.value.funds
    .filter((f) => f.gain !== null && f.gain < 0)
    .sort((a, b) => (a.gain ?? 0) - (b.gain ?? 0))
    .slice(0, 3),
)

function openScheme(securityId: number): void {
  void router.push({ name: 'scheme-detail', params: { investorId: investorId.value, securityId } })
}
</script>

<template>
  <section class="dashboard">
    <header class="page-head">
      <div>
        <h1>Dashboard</h1>
        <p class="sub">
          {{ investorName }} ·
          <span v-if="summary.navsStale" class="stale-navs" title="Prices haven't refreshed recently">
            <i class="pi pi-exclamation-triangle" aria-hidden="true" /> NAVs as of {{ summary.navsAsOf }}
          </span>
          <template v-else>{{ summary.asOf }}</template>
        </p>
      </div>
    </header>

    <div class="bento">
      <header class="hero span-8 card">
        <div class="hero-net">
          <p class="eyebrow">Net worth</p>
          <p class="hero-value">{{ formatInrCompact(heroNetWorth) }}</p>
          <p class="hero-exact">{{ formatInr(summary.netWorth) }}</p>
          <p class="hero-invested">Invested {{ formatInrCompact(summary.invested) }}</p>
        </div>
        <div class="hero-kpis">
          <div class="kpi">
            <span class="eyebrow">All-time return</span>
            <DeltaChip
              :amount="summary.totalReturnAmount"
              :percent="summary.totalReturnPercent ?? undefined"
              :value="summary.totalReturnAmount"
              size="md"
              compact
            />
          </div>
          <div class="kpi">
            <span class="eyebrow">XIRR</span>
            <DeltaChip
              v-if="summary.xirr !== null"
              :percent="summary.xirr"
              :value="summary.xirr"
              size="md"
            />
            <span v-else class="kpi-na">Needs more history</span>
          </div>
          <div class="kpi">
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
          <div class="kpi">
            <span class="eyebrow">Holdings</span>
            <span class="kpi-val">{{ summary.holdingsCount }}</span>
          </div>
        </div>
      </header>

      <IntegrityHealthCard class="span-4" :rollup="rollup" :review-to="integrityTo" />
    </div>

    <nav class="asset-tabs" aria-label="Asset class">
      <RouterLink class="asset-tab" :class="{ active: activeTab === 'all' }" :to="tabTo()"
        >All</RouterLink
      >
      <RouterLink class="asset-tab" :class="{ active: activeTab === 'mf' }" :to="tabTo('mf')"
        >Mutual funds</RouterLink
      >
      <RouterLink class="asset-tab" :class="{ active: activeTab === 'stocks' }" :to="tabTo('stocks')"
        >Stocks</RouterLink
      >
      <button
        type="button"
        class="asset-tab more"
        aria-haspopup="true"
        aria-label="More asset classes"
        @click="(e) => moreOp?.toggle(e)"
      >
        <i class="pi pi-ellipsis-h" /> More
      </button>
    </nav>

    <Popover ref="moreOp">
      <div class="more-pop">
        <p class="more-head">On the roadmap</p>
        <ul class="more-list">
          <li v-for="a in plannedAssets" :key="a.label">
            <i :class="a.icon" aria-hidden="true" /><span>{{ a.label }}</span>
          </li>
        </ul>
        <a class="more-vote" :href="POLL_URL" target="_blank" rel="noopener noreferrer">
          <i class="pi pi-thumbs-up" aria-hidden="true" /> Vote for what's next →
        </a>
      </div>
    </Popover>

    <div v-if="activeTab === 'all'" class="bento">
      <article ref="chartRegion" class="span-4 card chart-card">
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

      <article class="span-8 card chart-card">
        <div class="chart-head">
          <div class="head-titles">
            <h2>Value over time</h2>
            <span class="head-caption">Mutual funds · current value vs invested</span>
          </div>
          <SelectButton
            v-if="valuationReady && loadCharts"
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
        <template v-if="!valuationReady">
          <div class="chart-placeholder value-placeholder" aria-hidden="true" />
          <p class="chart-progress">
            Portfolio valuation in progress — refresh in a bit. Showing values as of
            your latest statement meanwhile.
          </p>
        </template>
        <PortfolioValueChart v-else-if="loadCharts" :data="summary.valueSeries" />
        <div v-else class="chart-placeholder value-placeholder" aria-hidden="true" />
      </article>

      <article v-if="topContributors.length" class="span-12 card movers">
        <h2>Contribution to returns</h2>
        <p class="movers-sub">Funds that have added the most to your portfolio so far.</p>
        <div class="movers-row">
          <button
            v-for="f in topContributors"
            :key="f.securityId"
            type="button"
            class="mover"
            @click="openScheme(f.securityId)"
          >
            <span class="mover-name">{{ f.name }}</span>
            <DeltaChip
              :amount="f.gain ?? undefined"
              :percent="f.returnPct ?? undefined"
              :value="f.gain ?? undefined"
              size="sm"
            />
          </button>
        </div>
        <template v-if="detractors.length">
          <p class="movers-sub detractor-label">Held back returns</p>
          <div class="movers-row">
            <button
              v-for="f in detractors"
              :key="f.securityId"
              type="button"
              class="mover"
              @click="openScheme(f.securityId)"
            >
              <span class="mover-name">{{ f.name }}</span>
              <DeltaChip
                :amount="f.gain ?? undefined"
                :percent="f.returnPct ?? undefined"
                :value="f.gain ?? undefined"
                size="sm"
              />
            </button>
          </div>
        </template>
      </article>

      <article class="span-12 card">
        <h2>Top holdings</h2>
        <DataTable
          v-if="loadCharts"
          :value="summary.topHoldings"
          data-key="securityId"
          class="holdings clickable-rows"
          size="small"
          @row-click="(e) => openScheme(e.data.securityId)"
        >
          <Column field="name" header="Holding">
            <template #body="{ data }">
              <div class="holding-name">
                <span>{{ data.name }}</span>
                <small>{{ data.assetClass }}</small>
              </div>
            </template>
          </Column>
          <Column header="Value" class="num">
            <template #body="{ data }">{{ formatInr(data.value) }}</template>
          </Column>
          <Column header="Units" class="num">
            <template #body="{ data }">{{ formatUnits(data.units) }}</template>
          </Column>
          <Column header="Return" class="num">
            <template #body="{ data }">
              <DeltaChip v-if="data.returnPct !== null" :percent="data.returnPct" :value="data.returnPct" size="sm" />
              <span v-else class="muted">—</span>
            </template>
          </Column>
          <Column header="Integrity">
            <template #body="{ data }">
              <IntegrityBadge :status="data.integrity" size="sm" />
            </template>
          </Column>
        </DataTable>
        <div v-else class="table-placeholder" aria-hidden="true" />
      </article>
    </div>

    <DashboardFunds
      v-else-if="activeTab === 'mf'"
      :by-category="summary.mfByCategory"
      :by-amc="summary.mfByAmc"
      :funds="summary.funds"
      :total="summary.mfTotal"
      @select="openScheme"
    />

    <DashboardStocks
      v-else-if="activeTab === 'stocks'"
      :stocks="summary.stocks"
      :total="summary.stockTotal"
      @select="openScheme"
    />
  </section>
</template>

<style scoped>
.dashboard {
  padding: var(--fm-space-6);
  max-width: var(--fm-content-max);
  margin: 0 auto;
  width: 100%;
}

.page-head {
  margin-bottom: var(--fm-space-5);
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
/* NAV-feed staleness marker: qualifies price recency without alarming about value. */
.page-head .sub .stale-navs {
  color: var(--p-amber-600, #d97706);
  font-weight: 600;
}
.page-head .sub .stale-navs .pi {
  font-size: 0.75rem;
}

.bento {
  display: grid;
  /* minmax(0, …) so a track can shrink past its content's intrinsic width;
     the default `1fr` is `minmax(auto, 1fr)` and would force sideways scroll. */
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: var(--fm-space-5);
}

.span-3 { grid-column: span 3; }
.span-4 { grid-column: span 4; }
.span-6 { grid-column: span 6; }
.span-8 { grid-column: span 8; }
.span-12 { grid-column: span 12; }

/* Every grid item must also opt out of the auto min-width floor. */
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

.chart-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--fm-space-2) var(--fm-space-3);
  /* In a narrow card the toggle can't sit beside the title — let it wrap to its
     own line rather than squeezing the segments until labels truncate. */
  flex-wrap: wrap;
  margin-bottom: var(--fm-space-2);
}
.chart-head h2 {
  margin: 0;
}
/* Keep the toggle intact: never wrap a segment's label, never shrink it. */
.chart-head :deep(.p-selectbutton) {
  flex-shrink: 0;
}
.chart-head :deep(.p-togglebutton),
.chart-head :deep(.p-togglebutton-label) {
  white-space: nowrap;
}
.head-caption {
  display: block;
  margin-top: 0.15rem;
  font-size: 0.75rem;
  color: var(--fm-text-muted);
}

/* Contribution-to-returns strip. */
.movers .movers-sub {
  margin: 0 0 var(--fm-space-3);
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.movers .detractor-label {
  margin-top: var(--fm-space-4);
}
.movers-row {
  display: flex;
  gap: var(--fm-space-3);
  flex-wrap: wrap;
}
.mover {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.35rem;
  min-width: 0;
  max-width: 16rem;
  padding: 0.6rem 0.8rem;
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-lg, 0.75rem);
  background: var(--fm-surface-raised);
  cursor: pointer;
  text-align: left;
  font: inherit;
  transition: border-color var(--fm-dur-fast) var(--fm-ease);
}
.mover:hover {
  border-color: var(--fm-border);
}
.mover-name {
  max-width: 14rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.8125rem;
  font-weight: 600;
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
    linear-gradient(90deg, transparent 0, color-mix(in srgb, var(--fm-border-subtle) 32%, transparent) 50%, transparent 100%),
    var(--fm-surface-raised);
}
.donut-placeholder {
  height: 260px;
}
.value-placeholder {
  height: 280px;
}
.chart-progress {
  margin: var(--fm-space-2) 0 0;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.table-placeholder {
  height: 12rem;
  border-radius: var(--fm-radius-sm);
  background:
    linear-gradient(90deg, transparent 0, color-mix(in srgb, var(--fm-border-subtle) 32%, transparent) 50%, transparent 100%),
    var(--fm-surface-raised);
}

/* ---- asset-class tab strip ---- */
.asset-tabs {
  display: flex;
  gap: var(--fm-space-1, 0.25rem);
  border-bottom: 1px solid var(--fm-border-subtle);
  margin: var(--fm-space-5) 0 var(--fm-space-4);
  overflow-x: auto;
}
.asset-tab {
  padding: 0.6rem 0.95rem;
  text-decoration: none;
  white-space: nowrap;
  color: var(--fm-text-muted);
  font-weight: 600;
  font-size: 0.9rem;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color var(--fm-dur-fast) var(--fm-ease);
}
.asset-tab:hover:not(.disabled) {
  color: var(--fm-text);
}
.asset-tab.active {
  color: var(--p-primary-color);
  border-bottom-color: var(--p-primary-color);
}
.asset-tab.more {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  font: inherit;
  font-weight: 600;
  font-size: 0.9rem;
  color: var(--fm-text-muted);
}
.asset-tab.more:hover {
  color: var(--fm-text);
}
.asset-tab.more .pi-ellipsis-h {
  font-size: 0.8rem;
}

/* "More" popover: planned asset classes + a vote CTA. */
.more-pop {
  min-width: 13rem;
  padding: 0.25rem;
}
.more-head {
  margin: 0 0 0.5rem;
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--fm-text-muted);
}
.more-list {
  list-style: none;
  margin: 0 0 0.6rem;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
.more-list li {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.4rem 0.5rem;
  border-radius: var(--fm-radius-sm);
  color: var(--fm-text);
  font-size: 0.875rem;
}
.more-list li i {
  color: var(--fm-text-subtle);
  width: 1rem;
  text-align: center;
}
.more-vote {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  padding: 0.55rem 0.6rem;
  border-top: 1px solid var(--fm-border-subtle);
  margin-top: 0.2rem;
  color: var(--p-primary-color);
  font-weight: 600;
  font-size: 0.875rem;
  text-decoration: none;
}
.more-vote:hover {
  color: var(--fm-accent-hover, var(--p-primary-color));
  opacity: 0.85;
}

/* ---- hero band ---- */
.hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--fm-space-5);
  flex-wrap: wrap;
}
.eyebrow {
  margin: 0;
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--fm-text-muted);
}
.hero-net {
  min-width: 0;
}
.hero-value {
  margin: 0.25rem 0 0;
  font-size: 2.4rem;
  line-height: 1.05;
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
.hero-invested {
  margin: 0.4rem 0 0;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
/* KPI grid fills the hero's right half: two return bases + 1D + holdings count.
   Replaces the old toggle so both Absolute and XIRR are visible at once. */
.hero-kpis {
  flex: 1;
  min-width: 16rem;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--fm-space-4) var(--fm-space-5);
  align-content: center;
}
.kpi {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  align-items: flex-start;
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
/* On a phone the hero stacks; one KPI per row keeps the numbers readable. */
@media (max-width: 480px) {
  .hero-kpis {
    grid-template-columns: 1fr;
  }
}

.holding-name {
  display: flex;
  flex-direction: column;
}
.holding-name small {
  color: var(--fm-text-muted);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

:deep(.holdings .num) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
/* On a narrow screen the table scrolls within its card rather than widening
   the page. */
:deep(.holdings .p-datatable-table-container) {
  overflow-x: auto;
}

/* Top-holdings rows drill into the scheme detail. */
:deep(.clickable-rows .p-datatable-tbody > tr) {
  cursor: pointer;
}
.muted {
  color: var(--fm-text-muted);
}

/* Re-flow to a single column on narrow viewports. */
@media (max-width: 1024px) {
  .span-3, .span-4, .span-6, .span-8 { grid-column: span 6; }
}
@media (max-width: 768px) {
  .span-3, .span-4, .span-6, .span-8, .span-12 { grid-column: span 12; }
}
/* Phone: trim the chrome so content keeps the width. */
@media (max-width: 640px) {
  .dashboard { padding: var(--fm-space-4); }
  .bento { gap: var(--fm-space-4); }
  .card { padding: var(--fm-space-4); }
}
</style>
