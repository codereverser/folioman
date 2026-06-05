<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, toRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Popover from 'primevue/popover'
import IntegrityHealthCard from '@/components/IntegrityHealthCard.vue'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import DeltaChip from '@/components/DeltaChip.vue'
import DashboardFunds from '@/views/dashboard/DashboardFunds.vue'
import { useDashboard, type RangeKey } from '@/composables/useDashboard'
import { useCountUp } from '@/composables/useCountUp'
import { useRosterStore } from '@/stores/roster'
import { useUiStore } from '@/stores/ui'
import { formatInr, formatUnits } from '@/utils/format'

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
const integrityTo = computed(() => ({
  name: 'integrity',
  params: { investorId: investorId.value },
}))

const ranges: { label: string; value: RangeKey }[] = [
  { label: '6M', value: '6M' },
  { label: '1Y', value: '1Y' },
  { label: 'All', value: 'All' },
]

// Hero: net worth counts up; the all-time return % switches between the simple
// absolute return and XIRR (money-weighted annualized, the headline default).
// (No CAGR: it assumes a single lump sum and a known holding period — wrong for a
// multi-cashflow SIP portfolio, where XIRR is the correct annualized figure.)
const heroNetWorth = useCountUp(toRef(() => summary.value.netWorth))

type ReturnBasis = 'absolute' | 'xirr'
const returnBasis = ref<ReturnBasis>('xirr')
const basisOptions: { label: string; value: ReturnBasis }[] = [
  { label: 'Absolute', value: 'absolute' },
  { label: 'XIRR', value: 'xirr' },
]
const shownReturnPercent = computed<number | null>(() =>
  returnBasis.value === 'absolute' ? summary.value.totalReturnPercent : summary.value.xirr,
)

// Allocation breakdown grouping. Pre-multi-asset the asset-class view is a single
// "Mutual funds" slice, so the donut groups by equity/debt or fund house instead.
type AllocationGroup = 'category' | 'amc'
const allocationGroups: { label: string; value: AllocationGroup }[] = [
  { label: 'Equity/Debt', value: 'category' },
  { label: 'AMC', value: 'amc' },
]
const allocationGroup = ref<AllocationGroup>('category')
const allocationData = computed(() =>
  allocationGroup.value === 'amc'
    ? summary.value.allocationByAmc
    : summary.value.allocationByCategory,
)

// Asset-class tab from the route (deep-linkable): no segment = All, `/mf` = MF.
const activeTab = computed<'all' | 'mf'>(() => (route.params.assetTab === 'mf' ? 'mf' : 'all'))

// "More" tab → a popover listing planned asset classes + a link to vote on what's
// next (a GitHub Discussions poll). Keeps the tab strip tidy and frames these as
// "planned · vote" rather than promising delivery.
const POLL_URL = 'https://github.com/codereverser/folioman/discussions/52'
const plannedAssets: { label: string; icon: string }[] = [
  { label: 'Stocks', icon: 'pi pi-chart-bar' },
  { label: 'US stocks', icon: 'pi pi-globe' },
  { label: 'Gold', icon: 'pi pi-star' },
  { label: 'Crypto', icon: 'pi pi-bitcoin' },
  { label: 'Fixed deposits', icon: 'pi pi-wallet' },
  { label: 'Real estate', icon: 'pi pi-home' },
]
const moreOp = ref<InstanceType<typeof Popover>>()
function tabTo(asset?: 'mf') {
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
        <p class="sub">{{ investorName }} · {{ summary.asOf }}</p>
      </div>
    </header>

    <div class="bento">
      <header class="hero span-8 card">
        <div class="hero-net">
          <p class="eyebrow">Net worth</p>
          <p class="hero-value">{{ formatInr(heroNetWorth) }}</p>
          <p class="hero-invested">Invested {{ formatInr(summary.invested) }}</p>
        </div>
        <div class="hero-returns">
          <div class="ret">
            <div class="ret-head">
              <span class="eyebrow">All-time return</span>
              <SelectButton
                class="basis-toggle"
                :model-value="returnBasis"
                :options="basisOptions"
                option-label="label"
                option-value="value"
                :allow-empty="false"
                size="small"
                @update:model-value="(v: ReturnBasis | null) => v && (returnBasis = v)"
              />
            </div>
            <DeltaChip
              :amount="summary.totalReturnAmount"
              :percent="shownReturnPercent ?? undefined"
              :value="summary.totalReturnAmount"
              size="md"
            />
            <span v-if="returnBasis === 'xirr' && shownReturnPercent === null" class="ret-na"
              >XIRR needs more history</span
            >
          </div>
          <div class="ret ret-1d">
            <span class="eyebrow">1D return</span>
            <DeltaChip
              v-if="summary.dayChangeAmount !== null"
              :amount="summary.dayChangeAmount"
              :percent="summary.dayChangePercent ?? undefined"
              size="sm"
            />
            <span v-else class="muted">—</span>
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
          :center-label="formatInr(summary.netWorth)"
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
      :by-category="summary.allocationByCategory"
      :by-amc="summary.allocationByAmc"
      :funds="summary.funds"
      :total="summary.netWorth"
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
  gap: var(--fm-space-3);
  margin-bottom: var(--fm-space-2);
}
.chart-head h2 {
  margin: 0;
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
.hero-invested {
  margin: 0.4rem 0 0;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.hero-returns {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-4);
  align-items: flex-end;
  text-align: right;
}
.ret {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  align-items: flex-end;
}
.ret-head {
  display: flex;
  align-items: center;
  gap: var(--fm-space-3);
}
.ret-1d {
  opacity: 0.9;
}
.ret-na {
  font-size: 0.6875rem;
  color: var(--fm-text-subtle);
}
/* When the hero wraps to a narrow column, left-align the returns under the value. */
@media (max-width: 1024px) {
  .hero-returns {
    align-items: flex-start;
    text-align: left;
    width: 100%;
  }
  .ret {
    align-items: flex-start;
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
