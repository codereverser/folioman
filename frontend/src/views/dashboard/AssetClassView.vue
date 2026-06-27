<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Popover from 'primevue/popover'
import type { AllocationSlice } from '@/components/charts/AllocationDonut.vue'
import DeltaChip from '@/components/DeltaChip.vue'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import { useDashboard } from '@/composables/useDashboard'
import { useAssetClassSeries } from '@/composables/useAssetClassSeries'
import { useRosterStore } from '@/stores/roster'
import { useUiStore } from '@/stores/ui'
import { assetLabel, rampColor } from '@/utils/portfolio'
import { formatInr, formatInrCompact, formatUnits } from '@/utils/format'

const AllocationDonut = defineAsyncComponent(
  () => import('@/components/charts/AllocationDonut.vue'),
)
const PortfolioValueChart = defineAsyncComponent(
  () => import('@/components/charts/PortfolioValueChart.vue'),
)

const route = useRoute()
const router = useRouter()
const roster = useRosterStore()
const ui = useUiStore()

const investorId = computed(() => {
  const raw = route.params.investorId
  const n = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(n) ? n : (ui.selectedInvestorId ?? 0)
})
const assetType = computed(() => String(route.params.assetType ?? ''))
const classLabel = computed(() => assetLabel(assetType.value))
const investorName = computed(() => roster.investorName(investorId.value) ?? 'Investor')

const { summary, loading } = useDashboard(investorId)

// Holdings of this asset class only.
const holdings = computed(() =>
  summary.value.holdings
    .filter((h) => h.securityType === assetType.value)
    .sort((a, b) => b.value - a.value),
)
const securityIds = computed(() => holdings.value.map((h) => h.securityId))

// Class totals from the holdings (cost basis present only where known).
const totals = computed(() => {
  const value = holdings.value.reduce((s, h) => s + h.value, 0)
  const withCost = holdings.value.filter((h) => h.invested != null)
  const invested = withCost.reduce((s, h) => s + (h.invested ?? 0), 0)
  const gain = withCost.length ? withCost.reduce((s, h) => s + (h.gain ?? 0), 0) : null
  return {
    value,
    invested,
    gain,
    returnPct: gain != null && invested > 0 ? (gain / invested) * 100 : null,
  }
})

// Within-class composition: one slice per security.
const slices = computed<AllocationSlice[]>(() =>
  holdings.value.map((h, i) => ({ name: h.name, value: h.value, color: rampColor(i) })),
)

// Breakdown legend (with % of class). Capped to the top few so the card height
// stays in line with the donut; the long tail goes into a "+N more" popover.
const LEGEND_CAP = 5
const legend = computed(() =>
  slices.value.map((s) => ({
    ...s,
    pct: totals.value.value ? (s.value / totals.value.value) * 100 : 0,
  })),
)
const topLegend = computed(() => legend.value.slice(0, LEGEND_CAP))
const restLegend = computed(() => legend.value.slice(LEGEND_CAP))
const moreOp = ref<InstanceType<typeof Popover>>()

const { series } = useAssetClassSeries(investorId, securityIds)

function openScheme(securityId: number): void {
  void router.push({ name: 'scheme-detail', params: { investorId: investorId.value, securityId } })
}
// Quantity: a plain count — integers as-is, decimals only when fractional (so a
// 112-share lot reads "112", a 4,679.046-unit fund keeps its precision).
function qty(units: number): string {
  if (!units) return '—'
  return Number.isInteger(units) ? new Intl.NumberFormat('en-IN').format(units) : formatUnits(units)
}
</script>

<template>
  <section class="asset-page" :class="{ 'is-loading': loading }">
    <header class="page-head">
      <RouterLink class="back" :to="{ name: 'dashboard', params: { investorId } }">
        <i class="pi pi-arrow-left" aria-hidden="true" /> Dashboard
      </RouterLink>
      <h1>{{ classLabel }}</h1>
      <p class="sub">{{ investorName }} · {{ holdings.length }} holdings</p>
    </header>

    <!-- Hero: class value + the class's value-over-time as the main card. -->
    <article class="card hero">
      <div class="hero-head">
        <div class="hero-net">
          <p class="eyebrow">{{ classLabel }} value</p>
          <p class="hero-value">{{ formatInrCompact(totals.value) }}</p>
          <p class="hero-exact">{{ formatInr(totals.value) }}</p>
          <p class="hero-invested">
            Invested {{ formatInrCompact(totals.invested) }}
            <DeltaChip
              v-if="totals.returnPct !== null"
              class="hero-delta"
              :amount="totals.gain ?? undefined"
              :percent="totals.returnPct"
              :value="totals.gain ?? undefined"
              size="sm"
              compact
            />
          </p>
        </div>
      </div>

      <PortfolioValueChart
        v-if="series.length"
        :data="series"
        granularity="monthly"
        :window="null"
      />
      <p v-else class="chart-progress">
        No day-wise history for this class yet — snapshot holdings count toward value but not the
        trend.
      </p>
    </article>

    <!-- Composition: donut + breakdown as two cards in one row. -->
    <div class="bento">
      <article class="span-5 card">
        <h2>Composition</h2>
        <AllocationDonut
          v-if="slices.length"
          hide-legend
          :data="slices"
          :center-label="formatInrCompact(totals.value)"
        />
        <p v-else class="muted">No holdings.</p>
      </article>

      <article class="span-7 card">
        <h2>Breakdown</h2>
        <ul v-if="legend.length" class="comp-legend">
          <li v-for="s in topLegend" :key="s.name">
            <span class="sw" :style="{ background: s.color }" aria-hidden="true" />
            <span class="nm">{{ s.name }}</span>
            <span class="pc">{{ s.pct.toFixed(1) }}%</span>
            <span class="vl">{{ formatInr(s.value) }}</span>
          </li>
        </ul>
        <button
          v-if="restLegend.length"
          type="button"
          class="more-btn"
          @click="(e) => moreOp?.toggle(e)"
        >
          +{{ restLegend.length }} more
        </button>
        <Popover ref="moreOp">
          <ul class="comp-legend pop">
            <li v-for="s in restLegend" :key="s.name">
              <span class="sw" :style="{ background: s.color }" aria-hidden="true" />
              <span class="nm">{{ s.name }}</span>
              <span class="pc">{{ s.pct.toFixed(1) }}%</span>
              <span class="vl">{{ formatInr(s.value) }}</span>
            </li>
          </ul>
        </Popover>
      </article>
    </div>

    <!-- Securities: full width below. -->
    <article class="card">
      <h2>Securities</h2>
      <DataTable
        :value="holdings"
        data-key="securityId"
        size="small"
        class="securities clickable-rows"
        @row-click="(e) => openScheme(e.data.securityId)"
      >
        <Column field="name" header="Holding">
          <template #body="{ data }">
            <span class="h-name">{{ data.name }}</span>
          </template>
        </Column>
        <Column header="Quantity" class="num" header-class="num">
          <template #body="{ data }">{{ qty(data.units) }}</template>
        </Column>
        <Column header="Value" class="num" header-class="num">
          <template #body="{ data }">{{ formatInr(data.value) }}</template>
        </Column>
        <Column header="Return" class="num" header-class="num">
          <template #body="{ data }">
            <DeltaChip
              v-if="data.returnPct !== null"
              :percent="data.returnPct"
              :value="data.returnPct"
              size="sm"
            />
            <span v-else class="muted">—</span>
          </template>
        </Column>
        <Column header="Integrity">
          <template #body="{ data }"
            ><IntegrityBadge :status="data.integrity" size="sm"
          /></template>
        </Column>
      </DataTable>
    </article>
  </section>
</template>

<style scoped>
.asset-page {
  padding: var(--fm-space-6);
  max-width: var(--fm-content-max);
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-5);
}
.page-head h1 {
  margin: 0.4rem 0 0;
  font-size: 1.5rem;
  font-weight: 600;
}
.page-head .sub {
  margin: 0.15rem 0 0;
  color: var(--fm-text-muted);
}
.back {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  color: var(--fm-text-muted);
  text-decoration: none;
  font-size: 0.8125rem;
}
.back:hover {
  color: var(--fm-text);
}
.back .pi {
  font-size: 0.75rem;
}

/* Composition (donut) + Breakdown (legend) as two cards in a row. */
.bento {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: var(--fm-space-5);
}
.span-5 {
  grid-column: span 5;
}
.span-7 {
  grid-column: span 7;
}
.bento > * {
  min-width: 0;
}

.comp-legend {
  list-style: none;
  margin: 0;
  padding: 0;
}
.comp-legend li {
  display: flex;
  align-items: center;
  gap: var(--fm-space-3);
  padding: var(--fm-space-3) 0;
  border-bottom: 1px solid var(--fm-border-subtle);
  font-size: 0.875rem;
}
.comp-legend li:last-child {
  border-bottom: none;
}
.comp-legend .sw {
  width: 10px;
  height: 10px;
  border-radius: 3px;
  flex: none;
}
.comp-legend .nm {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.comp-legend .pc {
  color: var(--fm-text-muted);
  font-variant-numeric: tabular-nums;
}
.comp-legend .vl {
  min-width: 6rem;
  text-align: right;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.comp-legend.pop {
  min-width: 18rem;
  max-height: 22rem;
  overflow-y: auto;
}
.more-btn {
  margin-top: var(--fm-space-3);
  background: none;
  border: none;
  padding: 0.25rem 0;
  cursor: pointer;
  font: inherit;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--p-primary-color, var(--fm-verified));
}
.more-btn:hover {
  text-decoration: underline;
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
.hero-head {
  margin-bottom: var(--fm-space-3);
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
.hero-invested {
  margin: 0.35rem 0 0;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.chart-progress {
  margin: var(--fm-space-2) 0 0;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.h-name {
  font-weight: 500;
}
.muted {
  color: var(--fm-text-muted);
}

/* Blur figures while the class data loads, so momentary zeros read as loading. */
.asset-page.is-loading .hero-net,
.asset-page.is-loading .comp-legend,
.asset-page.is-loading .securities {
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
  .asset-page.is-loading .hero-net,
  .asset-page.is-loading .comp-legend,
  .asset-page.is-loading .securities {
    animation: none;
    opacity: 0.55;
  }
}
:deep(.securities .num) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
/* PrimeVue wraps the header label in a flex container, so text-align alone won't
   right-align it — push the header content to match the right-aligned cells. */
:deep(.securities th.num .p-datatable-column-header-content),
:deep(.securities th.num .p-column-header-content) {
  justify-content: flex-end;
}
:deep(.securities .p-datatable-table-container) {
  overflow-x: auto;
}
:deep(.clickable-rows .p-datatable-tbody > tr) {
  cursor: pointer;
}

@media (max-width: 1024px) {
  .span-5,
  .span-7 {
    grid-column: span 12;
  }
}
@media (max-width: 640px) {
  .asset-page {
    padding: var(--fm-space-4);
    gap: var(--fm-space-4);
  }
  .card {
    padding: var(--fm-space-4);
  }
}
</style>
