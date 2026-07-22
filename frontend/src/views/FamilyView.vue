<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, toRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Message from 'primevue/message'
import DeltaChip from '@/components/DeltaChip.vue'
import ReturnsStrip from '@/components/ReturnsStrip.vue'
import { useFamilyDashboard, type RangeKey } from '@/composables/useFamilyDashboard'
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

const familyId = computed(() => {
  const raw = route.params.familyId
  const n = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(n) ? n : (ui.selectedFamilyId ?? 0)
})
const familyName = computed(() => roster.familyName(familyId.value) ?? 'Family')

// Live aggregate + the full combined series (fetched once); the range toggle just
// windows it client-side via `valueWindow`, same as the investor dashboard.
const { summary, members, range, setRange, valueWindow, granularity, valuationReady, loading } =
  useFamilyDashboard(familyId)

// Hero combined value counts up; the delta under it tracks the selected range.
const heroTotal = useCountUp(toRef(() => summary.value.total))
const rangeLabel = computed(() => RANGE_LABEL[range.value])
const periodReturn = computed(() =>
  windowChange(summary.value.valueSeries, valueWindow.value?.from),
)

function openInvestor(investorId: number): void {
  ui.selectInvestor(investorId)
  void router.push({ name: 'dashboard', params: { investorId } })
}
</script>

<template>
  <section class="fm-page">
    <header class="fm-page-head">
      <div>
        <h1>{{ familyName }}</h1>
        <p class="sub">Combined portfolio · {{ summary.asOf }}</p>
      </div>
    </header>

    <!-- Hero: combined value + the value-over-time chart as the main card. -->
    <article ref="chartRegion" class="fm-card">
      <div class="fm-hero-head">
        <div class="fm-hero-net" :class="{ 'fm-blur-loading': loading }">
          <p class="fm-eyebrow">Combined value</p>
          <p class="fm-hero-value">{{ formatInrCompact(heroTotal) }}</p>
          <p class="fm-hero-exact">{{ formatInr(summary.total) }}</p>
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

      <template v-if="loadCharts && summary.valueSeries?.length">
        <PortfolioValueChart
          :data="summary.valueSeries"
          :granularity="granularity"
          :window="valueWindow"
        />
        <p v-if="!valuationReady" class="fm-chart-progress">
          Catching up the latest days — showing through the last computed day.
          <RouterLink :to="{ name: 'settings', params: { tab: 'navs' } }"
            >Check NAV freshness →</RouterLink
          >
        </p>
      </template>
      <template v-else-if="!valuationReady">
        <div class="fm-chart-placeholder fm-value-placeholder" aria-hidden="true" />
        <p class="fm-chart-progress">
          Portfolio valuation in progress — refresh in a bit. Showing values as of the latest
          statements meanwhile.
          <RouterLink :to="{ name: 'settings', params: { tab: 'navs' } }"
            >Check NAV freshness →</RouterLink
          >
        </p>
      </template>
      <p v-else-if="loadCharts" class="fm-chart-progress">
        No day-wise history yet — snapshot holdings count toward the combined value but not the
        trend. Import transaction statements (a CAS or a broker tradebook) to build the history.
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
        <span class="fm-eyebrow">Investors</span>
        <span class="fm-kpi-val">{{ summary.investorCount }}</span>
      </div>
      <div class="fm-stat">
        <span class="fm-eyebrow">Folios</span>
        <span class="fm-kpi-val">{{ summary.folioCount }}</span>
      </div>
    </section>

    <!-- Trailing returns: money-weighted (XIRR) over standard windows. -->
    <ReturnsStrip :returns="summary.periodReturns" />

    <!-- Allocation + member drill-ins. -->
    <div class="fm-bento">
      <article class="fm-span-8 fm-card">
        <h2>Allocation</h2>
        <AllocationDonut
          v-if="loadCharts"
          :data="summary.allocation"
          :center-label="formatInrCompact(summary.total)"
        />
        <div v-else class="fm-chart-placeholder fm-donut-placeholder" aria-hidden="true" />
      </article>

      <article class="fm-span-4 fm-card">
        <h2>Members</h2>
        <ul class="members">
          <li v-for="m in members" :key="m.id">
            <button type="button" class="member" @click="openInvestor(m.id)">
              <span class="member-name">{{ m.name }}</span>
              <span class="member-total">{{ formatInr(m.totalInr) }}</span>
              <i class="pi pi-angle-right" aria-hidden="true" />
            </button>
          </li>
          <li v-if="!members.length" class="muted empty">No investors in this family yet.</li>
        </ul>
      </article>
    </div>

    <article class="fm-card">
      <h2>Top holdings</h2>
      <DataTable
        v-if="loadCharts"
        :value="summary.topHoldings"
        data-key="securityId"
        class="holdings"
        size="small"
      >
        <Column field="name" header="Holding">
          <template #body="{ data }">
            <div class="holding-name">
              <span>{{ data.name }}</span>
              <small>{{ data.assetClass }}</small>
            </div>
          </template>
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
      </DataTable>
      <div v-else class="table-placeholder" aria-hidden="true" />
    </article>

    <Message severity="info" :closable="false">
      Capital-gains worksheets are per-investor — each PAN files its own return. Switch to a
      specific investor to download theirs.
    </Message>
  </section>
</template>

<style scoped>
.muted {
  color: var(--fm-text-muted);
}

.table-placeholder {
  height: 12rem;
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
/* On a narrow screen the table scrolls within its card rather than widening
   the page. */
:deep(.holdings .p-datatable-table-container) {
  overflow-x: auto;
}

.members {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-2);
}
.member {
  width: 100%;
  display: flex;
  align-items: center;
  gap: var(--fm-space-3);
  padding: var(--fm-space-3);
  background: var(--fm-surface-raised);
  border: 1px solid var(--fm-border);
  border-radius: var(--fm-radius-md);
  font: inherit;
  color: inherit;
  cursor: pointer;
  text-align: left;
  transition: border-color var(--fm-dur-fast) var(--fm-ease);
}
.member:hover {
  border-color: var(--fm-verified);
}
.member-name {
  flex: 1;
  font-weight: 500;
}
.member-total {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.member .pi {
  color: var(--fm-text-subtle);
}
.empty {
  padding: var(--fm-space-2) 0;
}
</style>
