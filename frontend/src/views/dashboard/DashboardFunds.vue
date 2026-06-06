<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from 'vue'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import AllocationDonut from '@/components/charts/AllocationDonut.vue'
import type { AllocationSlice } from '@/components/charts/AllocationDonut.vue'
import DeltaChip from '@/components/DeltaChip.vue'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import type { FundRow } from '@/composables/useDashboard'
import { formatInr, formatInrCompact, formatPercent } from '@/utils/format'

const SelectButton = defineAsyncComponent(() => import('primevue/selectbutton'))

const props = defineProps<{
  byCategory: AllocationSlice[]
  byAmc: AllocationSlice[]
  funds: FundRow[]
  total: number
}>()
const emit = defineEmits<{ (e: 'select', securityId: number): void }>()

type Grouping = 'category' | 'amc'
const grouping = ref<Grouping>('category')
const groupingOptions: { label: string; value: Grouping }[] = [
  { label: 'Equity/Debt', value: 'category' },
  { label: 'AMC', value: 'amc' },
]
// Provisioned now, enabled with the fund-intelligence work (needs a
// classification / look-through source). Rendered as disabled chips so the slot
// exists and the control reads as "more to come".
const gatedGroupings = ['Market cap', 'Sector']

const slices = computed(() => (grouping.value === 'amc' ? props.byAmc : props.byCategory))
const rows = computed(() => {
  const sum = slices.value.reduce((s, x) => s + x.value, 0) || 1
  return slices.value.map((s) => ({ ...s, pct: (s.value / sum) * 100 }))
})
const empty = computed(() => props.funds.length === 0)

// Per-fund list grouped by the active dimension. Group totals drive both the
// subheader value and the group ordering (largest group first; largest fund
// first within a group) — the order PrimeVue's subheader grouping renders.
const groupField = computed<'amc' | 'category'>(() => (grouping.value === 'amc' ? 'amc' : 'category'))
const groupTotals = computed(() => {
  const totals = new Map<string, number>()
  for (const f of props.funds) totals.set(f[groupField.value], (totals.get(f[groupField.value]) ?? 0) + f.value)
  return totals
})
const sortedFunds = computed(() => {
  const key = groupField.value
  const totals = groupTotals.value
  return [...props.funds].sort((a, b) => {
    const diff = (totals.get(b[key]) ?? 0) - (totals.get(a[key]) ?? 0)
    return diff !== 0 ? diff : b.value - a.value
  })
})
</script>

<template>
  <section class="funds">
    <div class="funds-head">
      <h2>Mutual funds breakdown</h2>
      <div class="groupings" role="group" aria-label="Group funds by">
        <SelectButton
          :model-value="grouping"
          :options="groupingOptions"
          option-label="label"
          option-value="value"
          :allow-empty="false"
          size="small"
          @update:model-value="(v: Grouping | null) => v && (grouping = v)"
        />
        <span
          v-for="g in gatedGroupings"
          :key="g"
          v-tooltip.bottom="'Comes with Fund Intelligence'"
          class="gated-chip"
          >{{ g }}</span
        >
      </div>
    </div>

    <p v-if="empty" class="empty">No priced mutual funds yet.</p>
    <template v-else>
      <div class="funds-grid">
        <article class="card donut-card">
          <AllocationDonut :data="slices" :center-label="formatInrCompact(total)" />
        </article>
        <article class="card breakdown-card">
          <ul class="breakdown">
            <li v-for="r in rows" :key="r.name">
              <span class="dot" :style="{ background: r.color }" aria-hidden="true" />
              <span class="b-name">{{ r.name }}</span>
              <span class="b-pct">{{ r.pct.toFixed(1) }}%</span>
              <span class="b-val">{{ formatInrCompact(r.value) }}</span>
            </li>
          </ul>
        </article>
      </div>

      <article class="card fund-list">
        <DataTable
          :value="sortedFunds"
          data-key="securityId"
          row-group-mode="subheader"
          :group-rows-by="groupField"
          size="small"
          class="funds-table clickable-rows"
          @row-click="(e) => emit('select', e.data.securityId)"
        >
          <template #groupheader="{ data }">
            <span class="grp">{{ data[groupField] }}</span>
            <span class="grp-total">{{ formatInrCompact(groupTotals.get(data[groupField]) ?? 0) }}</span>
          </template>
          <Column field="name" header="Fund" />
          <Column header="Value" class="num">
            <template #body="{ data }">{{ formatInr(data.value) }}</template>
          </Column>
          <Column header="Return" class="num">
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
          <Column header="XIRR" class="num">
            <template #body="{ data }">
              <span v-if="data.xirr !== null" class="xirr">{{ formatPercent(data.xirr) }}</span>
              <span v-else class="muted">—</span>
            </template>
          </Column>
          <Column header="Integrity">
            <template #body="{ data }">
              <IntegrityBadge :status="data.integrity" size="sm" />
            </template>
          </Column>
          <!-- Spacer column: in subheader row-group mode PrimeVue renders the group
               header cell with colspan = (columns − 1), so the family band stops one
               column short of the table edge. This hidden throwaway column absorbs
               that offset so the band spans the full visible width. Do not remove.
               See: https://github.com/primefaces/primevue/issues/3685#issuecomment-2107187144 -->
          <Column style="display: none" />
        </DataTable>
      </article>
    </template>
  </section>
</template>

<style scoped>
.funds-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fm-space-4);
  flex-wrap: wrap;
  margin-bottom: var(--fm-space-4);
}
.funds-head h2 {
  margin: 0;
  font-size: 1.125rem;
  font-weight: 600;
}
.groupings {
  display: flex;
  align-items: center;
  gap: var(--fm-space-2);
  flex-wrap: wrap;
}
.gated-chip {
  font-size: 0.75rem;
  padding: 0.3rem 0.6rem;
  border-radius: var(--fm-radius-pill);
  border: 1px dashed var(--fm-border);
  color: var(--fm-text-subtle);
  cursor: help;
}

.funds-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--fm-space-5);
  margin-bottom: var(--fm-space-5);
}
.card {
  padding: var(--fm-space-5);
  background: var(--fm-surface);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
  box-shadow: var(--fm-shadow-sm);
}

.breakdown {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.breakdown li {
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  align-items: center;
  gap: var(--fm-space-3);
  padding: 0.7rem 0;
  border-top: 1px solid var(--fm-border-subtle);
  font-variant-numeric: tabular-nums;
}
.breakdown li:first-child {
  border-top: none;
}
.dot {
  width: 0.7rem;
  height: 0.7rem;
  border-radius: 50%;
}
.b-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.b-pct {
  color: var(--fm-text-muted);
  font-size: 0.8125rem;
}
.b-val {
  font-weight: 600;
}
.empty {
  color: var(--fm-text-muted);
}

/* Per-fund grouped table. */
.fund-list :deep(.num) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.fund-list :deep(.clickable-rows .p-datatable-tbody > tr) {
  cursor: pointer;
}
.fund-list :deep(.p-datatable-row-group-header) {
  background: var(--fm-surface-raised);
}
.fund-list :deep(.p-datatable-row-group-header > td) {
  padding: 0.5rem 0.75rem;
}
.grp {
  font-weight: 600;
}
.grp-total {
  margin-left: var(--fm-space-3);
  color: var(--fm-text-muted);
  font-variant-numeric: tabular-nums;
}
.xirr {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.muted {
  color: var(--fm-text-muted);
}

@media (max-width: 900px) {
  .funds-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
