<script setup lang="ts">
import { computed } from 'vue'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import DeltaChip from '@/components/DeltaChip.vue'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import type { StockRow } from '@/composables/useDashboard'
import { formatInr, formatInrCompact, formatNav, formatPercent, formatUnits } from '@/utils/format'

const props = defineProps<{
  stocks: StockRow[]
  total: number
}>()
const emit = defineEmits<{ (e: 'select', securityId: number): void }>()

const empty = computed(() => props.stocks.length === 0)
const sorted = computed(() => [...props.stocks].sort((a, b) => b.value - a.value))
// The exchange ticker is the readable identity for an equity — the eCAS name is
// often a long descriptive string ("EQUITY SHARES WITH FACE VALUE RE.1…").
// Fall back to the name when no symbol resolved yet.
function ticker(s: StockRow): string {
  return s.symbol || s.name
}
</script>

<template>
  <section class="stocks">
    <div class="stocks-head">
      <h2>Stocks breakdown</h2>
      <span v-if="!empty" class="total">{{ formatInrCompact(total) }}</span>
    </div>

    <p v-if="empty" class="empty">
      No priced stocks yet. Equities from an eCAS snapshot price once their symbol
      resolves and price history backfills.
    </p>
    <article v-else class="card stock-list">
      <DataTable
        :value="sorted"
        data-key="securityId"
        size="small"
        class="stocks-table clickable-rows"
        @row-click="(e) => emit('select', e.data.securityId)"
      >
        <Column field="symbol" header="Stock">
          <template #body="{ data }">
            <div class="stock-cell">
              <span class="stock-ticker">{{ ticker(data) }}</span>
              <span class="stock-meta">
                <span v-if="data.symbol" class="stock-name">{{ data.name }}</span>
                <span v-if="data.price != null">· LTP {{ formatNav(data.price) }}</span>
                <span v-if="data.avgCost != null">· Avg {{ formatNav(data.avgCost) }}</span>
                <span
                  v-if="data.dayChangeAmount != null"
                  class="day"
                  :class="data.dayChangeAmount >= 0 ? 'up' : 'down'"
                >
                  · 1D {{ formatInr(data.dayChangeAmount) }}
                  <template v-if="data.dayChangePercent != null"
                    >({{ formatPercent(data.dayChangePercent) }})</template
                  >
                </span>
              </span>
            </div>
          </template>
        </Column>
        <Column header="Value" class="num">
          <template #body="{ data }">{{ formatInr(data.value) }}</template>
        </Column>
        <Column header="Shares" class="num">
          <template #body="{ data }">{{ formatUnits(data.units) }}</template>
        </Column>
        <Column header="Return" class="num">
          <template #body="{ data }">
            <DeltaChip
              v-if="data.returnPct !== null"
              :amount="data.gain ?? undefined"
              :percent="data.returnPct"
              :value="data.returnPct"
              size="sm"
              compact
            />
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
  </section>
</template>

<style scoped>
.stocks-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--fm-space-4);
  flex-wrap: wrap;
  margin-bottom: var(--fm-space-4);
}
.stocks-head h2 {
  margin: 0;
  font-size: 1.125rem;
  font-weight: 600;
}
.total {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--fm-text-muted);
}
.empty {
  color: var(--fm-text-muted);
}
.card {
  padding: var(--fm-space-5);
  background: var(--fm-surface);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
  box-shadow: var(--fm-shadow-sm);
}

.stock-list :deep(.num) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.stock-list :deep(.clickable-rows .p-datatable-tbody > tr) {
  cursor: pointer;
}
/* Card scrolls internally on a narrow screen rather than widening the page. */
.stock-list :deep(.p-datatable-table-container) {
  overflow-x: auto;
}

.stock-cell {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.stock-ticker {
  font-weight: 600;
}
.stock-meta {
  font-size: 0.75rem;
  color: var(--fm-text-muted);
  font-variant-numeric: tabular-nums;
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
}
.stock-name {
  max-width: 18rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stock-meta .day.up {
  color: var(--fm-gain);
}
.stock-meta .day.down {
  color: var(--fm-loss);
}
.muted {
  color: var(--fm-text-muted);
}
</style>
