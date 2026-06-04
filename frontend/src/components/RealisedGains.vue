<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import type { CapitalGains } from '@/composables/useCapitalGains'
import type { IntegrityRow } from '@/stores/integrity'
import { formatInr, formatUnits } from '@/utils/format'

const props = defineProps<{
  gains: CapitalGains | null
  excluded: IntegrityRow[]
  investorId: number
}>()

const router = useRouter()
const rows = computed(() => props.gains?.rows ?? [])
const stcg = computed(() => Number(props.gains?.stcg_total ?? 0))
const ltcg = computed(() => Number(props.gains?.ltcg_total ?? 0))

const integrityTo = computed(() => ({ name: 'integrity', params: { investorId: props.investorId } }))

function openScheme(securityId: number | null): void {
  if (securityId == null) return
  void router.push({ name: 'scheme-detail', params: { investorId: props.investorId, securityId } })
}

const EXCLUDED_REASON: Record<string, string> = {
  snapshot_only: 'Snapshot only — no transaction history, so gains can’t be computed.',
  mismatch: 'Units don’t reconcile — resolve before relying on any gain figure.',
  user_acknowledged: 'Acknowledged gap — deliberately left out.',
  unknown: 'Not yet reconciled.',
}
function reason(status: string): string {
  return EXCLUDED_REASON[status] ?? 'Left out.'
}
</script>

<template>
  <div class="realised-gains">
    <section class="totals">
      <div class="stat">
        <span class="label">Long-term (LTCG)</span>
        <span class="amount" :class="{ neg: ltcg < 0 }">{{ formatInr(ltcg) }}</span>
      </div>
      <div class="stat">
        <span class="label">Short-term (STCG)</span>
        <span class="amount" :class="{ neg: stcg < 0 }">{{ formatInr(stcg) }}</span>
      </div>
    </section>

    <section class="included">
      <h2>Realised gains <span class="count">{{ rows.length }}</span></h2>
      <DataTable
        v-if="rows.length"
        :value="rows"
        class="gains-table"
        size="small"
        :pt="{ table: { style: 'min-width: 44rem' } }"
      >
        <Column header="Holding">
          <template #body="{ data }">
            <button class="holding-name" type="button" @click="openScheme(data.security_id)">
              <span>{{ data.name }}</span>
              <small>{{ data.isin }}</small>
            </button>
          </template>
        </Column>
        <Column header="Term">
          <template #body="{ data }">
            <span class="term" :class="data.term">{{ data.term === 'long' ? 'LTCG' : 'STCG' }}</span>
          </template>
        </Column>
        <Column header="Units" class="num">
          <template #body="{ data }">{{ formatUnits(data.units) }}</template>
        </Column>
        <Column header="Sale value" class="num">
          <template #body="{ data }">{{ formatInr(data.sale_value) }}</template>
        </Column>
        <Column header="Cost" class="num">
          <template #body="{ data }">{{ formatInr(data.cost) }}</template>
        </Column>
        <Column header="Gain" class="num">
          <template #body="{ data }">
            <span :class="{ neg: Number(data.gain) < 0 }">{{ formatInr(data.gain) }}</span>
          </template>
        </Column>
      </DataTable>
      <p v-else class="empty">
        No realised gains in this year. Redeem a fully-reconciled holding (or pick another
        year) and they’ll appear here.
      </p>
    </section>

    <section v-if="excluded.length" class="excluded">
      <header class="ex-head-row">
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
.realised-gains {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-6);
}

.totals {
  display: flex;
  gap: var(--fm-space-4);
  flex-wrap: wrap;
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: var(--fm-space-4) var(--fm-space-5);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
  background: var(--fm-surface);
  min-width: 12rem;
}
.stat .label {
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.stat .amount {
  font-size: 1.4rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.amount.neg,
.neg {
  color: var(--fm-critical);
}

h2 {
  margin: 0 0 var(--fm-space-3);
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

.term {
  font-size: 0.6875rem;
  font-weight: 600;
  padding: 0.05rem 0.45rem;
  border-radius: var(--fm-radius-pill);
}
.term.long {
  color: var(--fm-verified);
  background: var(--fm-verified-bg);
}
.term.short {
  color: var(--fm-warn);
  background: var(--fm-warn-bg);
}

.holding-name {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.1rem;
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  text-align: left;
  color: var(--fm-text);
}
.holding-name:hover span {
  color: var(--p-primary-color);
  text-decoration: underline;
}
.holding-name small {
  color: var(--fm-text-subtle);
  font-size: 0.75rem;
}

.empty {
  margin: 0;
  padding: var(--fm-space-4);
  text-align: center;
  color: var(--fm-text-muted);
}

.ex-head-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--fm-space-3);
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
