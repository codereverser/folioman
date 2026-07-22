<script setup lang="ts">
import { computed, defineAsyncComponent, ref, toRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Popover from 'primevue/popover'
import type { AllocationSlice } from '@/components/charts/AllocationDonut.vue'
import DeltaChip from '@/components/DeltaChip.vue'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import { useDashboard, type RangeKey } from '@/composables/useDashboard'
import { useAssetClassSeries } from '@/composables/useAssetClassSeries'
import { useCountUp } from '@/composables/useCountUp'
import { useRangeWindow } from '@/composables/useRangeWindow'
import { useRosterStore } from '@/stores/roster'
import { useUiStore } from '@/stores/ui'
import { RANGE_LABEL, RANGE_OPTIONS, assetLabel, rampColor, windowChange } from '@/utils/portfolio'
import { formatInr, formatInrCompact, formatUnits } from '@/utils/format'

const AllocationDonut = defineAsyncComponent(
  () => import('@/components/charts/AllocationDonut.vue'),
)
const PortfolioValueChart = defineAsyncComponent(
  () => import('@/components/charts/PortfolioValueChart.vue'),
)
const SelectButton = defineAsyncComponent(() => import('primevue/selectbutton'))

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

// Class 1-day move: summed over the holdings that have one.
const dayChange = computed(() => {
  const known = holdings.value.filter((h) => h.dayChangeAmount != null)
  if (!known.length) return null
  const amount = known.reduce((s, h) => s + (h.dayChangeAmount ?? 0), 0)
  const prior = totals.value.value - amount
  return { amount, pct: prior ? (amount / prior) * 100 : undefined }
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

// The class trend is summed client-side over full history, so the range toggle
// windows it exactly like the portfolio chart — no refetch.
const { series } = useAssetClassSeries(investorId, securityIds)
const { range, setRange, valueWindow, granularity } = useRangeWindow('1Y')

// Hero class value counts up; the delta under it tracks the selected range.
const heroValue = useCountUp(toRef(() => totals.value.value))
const rangeLabel = computed(() => RANGE_LABEL[range.value])
const periodReturn = computed(() => windowChange(series.value, valueWindow.value?.from))

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
  <section class="fm-page">
    <header class="fm-page-head">
      <RouterLink class="back" :to="{ name: 'dashboard', params: { investorId } }">
        <i class="pi pi-arrow-left" aria-hidden="true" /> Dashboard
      </RouterLink>
      <h1>{{ classLabel }}</h1>
      <p class="sub">{{ investorName }} · {{ holdings.length }} holdings</p>
    </header>

    <!-- Hero: class value + the class's value-over-time as the main card. -->
    <article class="fm-card">
      <div class="fm-hero-head">
        <div class="fm-hero-net" :class="{ 'fm-blur-loading': loading }">
          <p class="fm-eyebrow">{{ classLabel }} value</p>
          <p class="fm-hero-value">{{ formatInrCompact(heroValue) }}</p>
          <p class="fm-hero-exact">{{ formatInr(totals.value) }}</p>
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
          <p class="fm-hero-invested">Invested {{ formatInrCompact(totals.invested) }}</p>
        </div>
        <SelectButton
          v-if="series.length"
          class="fm-hero-range"
          :model-value="range"
          :options="RANGE_OPTIONS"
          option-label="label"
          option-value="value"
          :allow-empty="false"
          size="small"
          @update:model-value="(v: RangeKey | null) => v && setRange(v)"
        />
      </div>

      <PortfolioValueChart
        v-if="series.length"
        :data="series"
        :granularity="granularity"
        :window="valueWindow"
      />
      <p v-else class="fm-chart-progress">
        No day-wise history for this class yet — snapshot holdings count toward value but not the
        trend.
      </p>
    </article>

    <!-- Headline metrics strip. -->
    <section class="fm-card fm-stat-strip" :class="{ 'fm-blur-loading': loading }">
      <div class="fm-stat">
        <span class="fm-eyebrow">All-time return</span>
        <DeltaChip
          v-if="totals.returnPct !== null"
          :amount="totals.gain ?? undefined"
          :percent="totals.returnPct"
          :value="totals.gain ?? undefined"
          size="md"
          compact
        />
        <span v-else class="fm-kpi-na">Needs cost basis</span>
      </div>
      <div class="fm-stat">
        <span class="fm-eyebrow">1D return</span>
        <DeltaChip
          v-if="dayChange !== null"
          :amount="dayChange.amount"
          :percent="dayChange.pct"
          size="sm"
          compact
        />
        <span v-else class="muted">—</span>
      </div>
      <div class="fm-stat">
        <span class="fm-eyebrow">Invested</span>
        <span class="fm-kpi-val">{{ formatInrCompact(totals.invested) }}</span>
      </div>
      <div class="fm-stat">
        <span class="fm-eyebrow">Holdings</span>
        <span class="fm-kpi-val">{{ holdings.length }}</span>
      </div>
    </section>

    <!-- Composition: donut + breakdown as two cards in one row. -->
    <div class="fm-bento">
      <article class="fm-span-5 fm-card">
        <h2>Composition</h2>
        <AllocationDonut
          v-if="slices.length"
          hide-legend
          :data="slices"
          :center-label="formatInrCompact(totals.value)"
        />
        <p v-else class="muted">No holdings.</p>
      </article>

      <article class="fm-span-7 fm-card">
        <h2>Breakdown</h2>
        <ul v-if="legend.length" class="comp-legend" :class="{ 'fm-blur-loading': loading }">
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
    <article class="fm-card">
      <h2>Securities</h2>
      <DataTable
        :value="holdings"
        data-key="securityId"
        size="small"
        class="securities clickable-rows"
        :class="{ 'fm-blur-loading': loading }"
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
.fm-page-head h1 {
  margin-top: 0.4rem;
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

.h-name {
  font-weight: 500;
}
.muted {
  color: var(--fm-text-muted);
}

:deep(.securities .p-datatable-table-container) {
  overflow-x: auto;
}
:deep(.clickable-rows .p-datatable-tbody > tr) {
  cursor: pointer;
}
</style>
