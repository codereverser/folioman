<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from 'vue'
import AllocationDonut from '@/components/charts/AllocationDonut.vue'
import type { AllocationSlice } from '@/components/charts/AllocationDonut.vue'
import { formatInr } from '@/utils/format'

const SelectButton = defineAsyncComponent(() => import('primevue/selectbutton'))

const props = defineProps<{
  byCategory: AllocationSlice[]
  byAmc: AllocationSlice[]
  total: number
}>()

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
const empty = computed(() => slices.value.length === 0)
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
    <div v-else class="funds-grid">
      <article class="card donut-card">
        <AllocationDonut :data="slices" :center-label="formatInr(total)" />
      </article>
      <article class="card breakdown-card">
        <ul class="breakdown">
          <li v-for="r in rows" :key="r.name">
            <span class="dot" :style="{ background: r.color }" aria-hidden="true" />
            <span class="b-name">{{ r.name }}</span>
            <span class="b-pct">{{ r.pct.toFixed(1) }}%</span>
            <span class="b-val">{{ formatInr(r.value) }}</span>
          </li>
        </ul>
      </article>
    </div>
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

@media (max-width: 900px) {
  .funds-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
