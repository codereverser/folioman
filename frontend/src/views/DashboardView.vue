<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import SelectButton from 'primevue/selectbutton'
import MetricCard from '@/components/MetricCard.vue'
import IntegrityHealthCard from '@/components/IntegrityHealthCard.vue'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import DeltaChip from '@/components/DeltaChip.vue'
import AllocationDonut from '@/components/charts/AllocationDonut.vue'
import PortfolioValueChart from '@/components/charts/PortfolioValueChart.vue'
import { useDashboard, type RangeKey } from '@/composables/useDashboard'
import { useRosterStore } from '@/stores/roster'
import { useUiStore } from '@/stores/ui'
import { formatInr, formatUnits } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const roster = useRosterStore()
const ui = useUiStore()

const investorId = computed(() => {
  const raw = route.params.investorId
  const n = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(n) ? n : (ui.selectedInvestorId ?? 0)
})
const investorName = computed(() => roster.investorName(investorId.value) ?? 'Investor')

// Live summary + net-worth series; the range toggle re-fetches the series.
const { summary, rollup, range, setRange } = useDashboard(investorId)
const integrityTo = { name: 'import' }

const ranges: { label: string; value: RangeKey }[] = [
  { label: '6M', value: '6M' },
  { label: '1Y', value: '1Y' },
  { label: 'All', value: 'All' },
]

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

      <article class="span-4 card chart-card">
        <h2>Allocation</h2>
        <AllocationDonut :data="summary.allocation" :center-label="formatInr(summary.netWorth)" />
      </article>

      <article class="span-8 card chart-card">
        <div class="chart-head">
          <h2>Portfolio value</h2>
          <SelectButton
            :model-value="range"
            :options="ranges"
            option-label="label"
            option-value="value"
            :allow-empty="false"
            size="small"
            @update:model-value="(v: RangeKey | null) => v && setRange(v)"
          />
        </div>
        <PortfolioValueChart :data="summary.valueSeries" />
      </article>

      <article class="span-12 card">
        <h2>Top holdings</h2>
        <DataTable
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
  grid-template-columns: repeat(12, 1fr);
  gap: var(--fm-space-5);
}

.span-3 { grid-column: span 3; }
.span-4 { grid-column: span 4; }
.span-6 { grid-column: span 6; }
.span-8 { grid-column: span 8; }
.span-12 { grid-column: span 12; }

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
</style>
