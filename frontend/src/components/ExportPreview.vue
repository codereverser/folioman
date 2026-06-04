<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import type { Schedule112A } from '@/composables/useTaxExport'
import type { IntegrityRow } from '@/stores/integrity'
import { formatInr } from '@/utils/format'

const props = defineProps<{
  report: Schedule112A | null
  excluded: IntegrityRow[]
  investorId: number
}>()

// The 15 ITR columns are positional; pull the few that tell the story inline and
// leave the full set to the CSV. Indices match SCHEDULE_112A_CSV_COLUMNS.
const COL = { isin: 2, name: 3, units: 4, saleValue: 6, deductions: 13, fmvUnit: 10, gain: 14 }
function cell(row: Record<string, string>, idx: number): string {
  const name = props.report?.columns[idx]
  return name ? (row[name] ?? '') : ''
}

const rows = computed(() => props.report?.rows ?? [])
const totalGain = computed(() =>
  rows.value.reduce((sum, r) => sum + (Number(cell(r, COL.gain)) || 0), 0),
)

const integrityTo = computed(() => ({ name: 'integrity', params: { investorId: props.investorId } }))

const EXCLUDED_REASON: Record<string, string> = {
  snapshot_only: 'Snapshot only — no transaction history, so gains can’t be computed.',
  mismatch: 'Units don’t reconcile — resolve before relying on any gain figure.',
  user_acknowledged: 'Acknowledged gap — deliberately left out of the worksheet.',
  unknown: 'Not yet reconciled.',
}
function reason(status: string): string {
  return EXCLUDED_REASON[status] ?? 'Left out of the worksheet.'
}
</script>

<template>
  <div class="export-preview">
    <section class="included">
      <header class="sec-head">
        <h2>Included <span class="count">{{ rows.length }}</span></h2>
        <p v-if="rows.length" class="total">
          Net long-term gain (for review): <strong>{{ formatInr(totalGain) }}</strong>
        </p>
      </header>

      <DataTable
        v-if="rows.length"
        :value="rows"
        class="gains-table"
        size="small"
        :pt="{ table: { style: 'min-width: 44rem' } }"
      >
        <Column header="Name">
          <template #body="{ data }">{{ cell(data, COL.name) }}<br /><small>{{ cell(data, COL.isin) }}</small></template>
        </Column>
        <Column header="Units" class="num">
          <template #body="{ data }">{{ cell(data, COL.units) }}</template>
        </Column>
        <Column header="Sale value" class="num">
          <template #body="{ data }">{{ cell(data, COL.saleValue) }}</template>
        </Column>
        <Column header="FMV/unit 31-Jan-18" class="num">
          <template #body="{ data }">{{ cell(data, COL.fmvUnit) || '—' }}</template>
        </Column>
        <Column header="Total deductions" class="num">
          <template #body="{ data }">{{ cell(data, COL.deductions) }}</template>
        </Column>
        <Column header="Gain (= sale − deductions)" class="num">
          <template #body="{ data }">{{ cell(data, COL.gain) }}</template>
        </Column>
      </DataTable>

      <p v-else class="empty">
        No tax-ready disposals in this year. Sell a fully-reconciled holding (or pick
        another year) and they’ll appear here.
      </p>
    </section>

    <section v-if="excluded.length" class="excluded">
      <header class="sec-head">
        <h2>Left out <span class="count">{{ excluded.length }}</span></h2>
        <RouterLink :to="integrityTo" class="review-link">Review in Data integrity →</RouterLink>
      </header>
      <ul class="excluded-list">
        <li v-for="row in excluded" :key="`${row.securityId}-${row.folioId}`">
          <div class="ex-head">
            <span class="ex-name">{{ row.name }}</span>
            <IntegrityBadge :status="row.status" size="sm" />
          </div>
          <p class="ex-reason">{{ reason(row.status) }}</p>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.export-preview {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-6);
}

.sec-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--fm-space-3);
  flex-wrap: wrap;
  margin-bottom: var(--fm-space-3);
}
.sec-head h2 {
  margin: 0;
  font-size: 1.05rem;
}
.count {
  display: inline-block;
  margin-left: 0.35rem;
  padding: 0 0.5rem;
  border-radius: var(--fm-radius-pill);
  background: var(--fm-surface-raised);
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.total {
  margin: 0;
  color: var(--fm-text-muted);
  font-size: 0.9375rem;
}
.total strong {
  color: var(--fm-text);
  font-variant-numeric: tabular-nums;
}

.gains-table {
  overflow-x: auto;
}
.gains-table small {
  color: var(--fm-text-subtle);
}
:deep(.num) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.empty {
  margin: 0;
  padding: var(--fm-space-4);
  text-align: center;
  color: var(--fm-text-muted);
}

.review-link {
  font-size: 0.875rem;
  color: var(--p-primary-color);
  text-decoration: none;
}
.review-link:hover {
  text-decoration: underline;
}

.excluded-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-3);
}
.excluded-list li {
  padding: var(--fm-space-3);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-lg);
  background: var(--fm-surface);
}
.ex-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fm-space-2);
}
.ex-name {
  font-weight: 600;
}
.ex-reason {
  margin: 0.3rem 0 0;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
</style>
