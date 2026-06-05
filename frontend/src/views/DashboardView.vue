<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import MetricCard from '@/components/MetricCard.vue'
import IntegrityHealthCard from '@/components/IntegrityHealthCard.vue'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import DeltaChip from '@/components/DeltaChip.vue'
import { useDashboard, type RangeKey } from '@/composables/useDashboard'
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
      <MetricCard
        class="span-6 hero-card"
        label="Net worth"
        :value="summary.netWorth"
        :delta-amount="summary.dayChangeAmount ?? undefined"
        :delta-percent="summary.dayChangePercent ?? undefined"
        hero
        count-up
      >
        <p class="hero-note">
          Invested {{ formatInr(summary.invested) }} ·
          <span class="total-return">
            Total return
            <DeltaChip :amount="summary.totalReturnAmount" :percent="summary.totalReturnPercent" size="sm" />
          </span>
        </p>
      </MetricCard>

      <MetricCard
        class="span-3"
        label="XIRR (annualised)"
        :value="summary.xirr"
        format="percent"
        :display="summary.xirr === null ? '—' : undefined"
      />

      <IntegrityHealthCard class="span-3" :rollup="rollup" :review-to="integrityTo" />

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
          <h2>Portfolio value</h2>
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
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--fm-space-2);
}
.chart-head h2 {
  margin: 0;
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

.hero-card .hero-note {
  margin: 0.5rem 0 0;
  font-size: 0.875rem;
  color: var(--fm-text-muted);
}
.total-return {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
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
