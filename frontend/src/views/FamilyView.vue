<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import SelectButton from 'primevue/selectbutton'
import Message from 'primevue/message'
import MetricCard from '@/components/MetricCard.vue'
import DeltaChip from '@/components/DeltaChip.vue'
import AllocationDonut from '@/components/charts/AllocationDonut.vue'
import PortfolioValueChart from '@/components/charts/PortfolioValueChart.vue'
import { useFamilyDashboard, type RangeKey } from '@/composables/useFamilyDashboard'
import { useRosterStore } from '@/stores/roster'
import { useUiStore } from '@/stores/ui'
import { formatInr } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const roster = useRosterStore()
const ui = useUiStore()

const familyId = computed(() => {
  const raw = route.params.familyId
  const n = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(n) ? n : (ui.selectedFamilyId ?? 0)
})
const familyName = computed(() => roster.familyName(familyId.value) ?? 'Family')

const { summary, members, range, setRange } = useFamilyDashboard(familyId)

const ranges: { label: string; value: RangeKey }[] = [
  { label: '6M', value: '6M' },
  { label: '1Y', value: '1Y' },
  { label: 'All', value: 'All' },
]

function openInvestor(investorId: number): void {
  ui.selectInvestor(investorId)
  void router.push({ name: 'dashboard', params: { investorId } })
}
</script>

<template>
  <section class="family">
    <header class="page-head">
      <div>
        <h1>{{ familyName }}</h1>
        <p class="sub">Combined portfolio · {{ summary.asOf }}</p>
      </div>
    </header>

    <Message severity="info" :closable="false" class="tax-note">
      Tax exports are per-investor — each PAN files its own ITR. Switch to a specific investor to
      download their Schedule 112A.
    </Message>

    <div class="bento">
      <MetricCard
        class="span-6 hero-card"
        label="Combined value"
        :value="summary.total"
        :delta-amount="summary.dayChangeAmount ?? undefined"
        :delta-percent="summary.dayChangePercent ?? undefined"
        hero
        count-up
      />
      <MetricCard
        class="span-2"
        label="XIRR (annualised)"
        :value="summary.xirr"
        format="percent"
        :display="summary.xirr === null ? '—' : undefined"
      />
      <MetricCard class="span-2" label="Investors" :value="summary.investorCount" format="raw" />
      <MetricCard class="span-2" label="Folios" :value="summary.folioCount" format="raw" />

      <article class="span-4 card chart-card">
        <h2>Allocation</h2>
        <AllocationDonut :data="summary.allocation" :center-label="formatInr(summary.total)" />
      </article>

      <article class="span-8 card chart-card">
        <div class="chart-head">
          <h2>Combined value</h2>
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

      <article class="span-7 card">
        <h2>Top holdings</h2>
        <DataTable :value="summary.topHoldings" data-key="securityId" class="holdings" size="small">
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
          <Column header="Return" class="num">
            <template #body="{ data }">
              <DeltaChip v-if="data.returnPct !== null" :percent="data.returnPct" :value="data.returnPct" size="sm" />
              <span v-else class="muted">—</span>
            </template>
          </Column>
        </DataTable>
      </article>

      <article class="span-5 card">
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
  </section>
</template>

<style scoped>
.family {
  padding: var(--fm-space-6);
  max-width: var(--fm-content-max);
  margin: 0 auto;
  width: 100%;
}

.page-head {
  margin-bottom: var(--fm-space-4);
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
.tax-note {
  margin-bottom: var(--fm-space-5);
}
.muted {
  color: var(--fm-text-muted);
}

.bento {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: var(--fm-space-5);
}
.span-2 { grid-column: span 2; }
.span-4 { grid-column: span 4; }
.span-5 { grid-column: span 5; }
.span-6 { grid-column: span 6; }
.span-7 { grid-column: span 7; }
.span-8 { grid-column: span 8; }

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
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-md);
  font: inherit;
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

/* Re-flow to a single column on narrow viewports. */
@media (max-width: 1024px) {
  .span-2, .span-4, .span-5, .span-6, .span-7, .span-8 {
    grid-column: span 12;
  }
}
</style>
