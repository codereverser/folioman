<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Message from 'primevue/message'
import MetricCard from '@/components/MetricCard.vue'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import NavHistoryChart from '@/components/charts/NavHistoryChart.vue'
import { useScheme } from '@/composables/useScheme'
import { useUiStore } from '@/stores/ui'
import { formatInr, formatInrPaise, formatUnits, formatDate } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const ui = useUiStore()

const investorId = computed(() => {
  const raw = route.params.investorId
  const n = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(n) ? n : (ui.selectedInvestorId ?? 0)
})
const securityId = computed(() => {
  const raw = route.params.securityId
  return Number(Array.isArray(raw) ? raw[0] : raw)
})

const { detail, notFound, navSeries, integrityStatus } = useScheme(investorId, securityId)

function num(v: string | number | null | undefined): number {
  const n = typeof v === 'string' ? Number(v) : (v ?? 0)
  return Number.isFinite(n) ? n : 0
}

// Header identity line: ISIN · AMFI.
const idLine = computed(() => {
  const s = detail.value?.security
  if (!s) return ''
  return [s.isin, s.amfi_code && `AMFI ${s.amfi_code}`].filter(Boolean).join(' · ')
})

const returnAmount = computed(() => {
  const d = detail.value
  if (!d || d.value_inr == null || d.invested_inr == null) return null
  return num(d.value_inr) - num(d.invested_inr)
})
const returnPct = computed(() =>
  detail.value?.return_pct == null ? null : detail.value.return_pct * 100,
)
const xirrPct = computed(() => (detail.value?.xirr == null ? null : detail.value.xirr * 100))

const TXN_TYPE_LABELS: Record<string, string> = {
  buy: 'Buy',
  sell: 'Sell',
  dividend: 'Dividend',
  bonus: 'Bonus',
  split: 'Split',
  transfer_in: 'Transfer in',
  transfer_out: 'Transfer out',
}
function txnLabel(t: string): string {
  return TXN_TYPE_LABELS[t] ?? t
}

function back(): void {
  void router.push({ name: 'dashboard', params: { investorId: investorId.value } })
}
</script>

<template>
  <section class="scheme">
    <Message v-if="notFound" severity="warn" :closable="false">
      This scheme isn’t in this investor’s portfolio.
      <a href="#" @click.prevent="back">Back to dashboard</a>.
    </Message>

    <template v-else-if="detail">
      <header class="page-head">
        <button class="back" type="button" @click="back"><i class="pi pi-arrow-left" /> Dashboard</button>
        <div class="title-row">
          <h1>{{ detail.security.name }}</h1>
          <IntegrityBadge :status="integrityStatus" size="lg" />
        </div>
        <div class="chips">
          <span v-if="detail.security.amc" class="chip">{{ detail.security.amc }}</span>
          <span v-if="detail.security.category" class="chip">{{ detail.security.category }}</span>
          <span class="chip subtle">{{ idLine }}</span>
        </div>
        <p v-if="detail.latest_nav != null" class="nav-line">
          Latest NAV <strong>{{ formatInrPaise(detail.latest_nav) }}</strong>
          <span class="muted"> · {{ formatDate(detail.latest_nav_date) }}</span>
        </p>
      </header>

      <div class="metrics">
        <MetricCard label="Invested" :value="num(detail.invested_inr)" />
        <MetricCard
          label="Current"
          :value="detail.value_inr == null ? null : num(detail.value_inr)"
          :display="detail.value_inr == null ? '—' : undefined"
          :delta-amount="detail.day_change_inr == null ? undefined : num(detail.day_change_inr)"
          :delta-percent="detail.day_change_pct == null ? undefined : detail.day_change_pct * 100"
        />
        <MetricCard
          label="Total return"
          :value="returnAmount"
          :display="returnAmount == null ? '—' : undefined"
          :delta-percent="returnPct ?? undefined"
        />
        <MetricCard
          label="XIRR"
          :value="xirrPct"
          format="percent"
          :display="xirrPct == null ? '—' : undefined"
        />
      </div>

      <article class="card">
        <h2>NAV history</h2>
        <NavHistoryChart v-if="navSeries.length" :data="navSeries" />
        <p v-else class="muted empty">No NAV history on file for this scheme yet.</p>
      </article>

      <article class="card">
        <h2>Transactions</h2>
        <Message v-if="!detail.has_transactions" severity="info" :closable="false">
          Snapshot only — this holding has no transaction history, so cost basis and the tax
          export aren’t available. Import a CAS with full history (or add transactions) to enable them.
        </Message>
        <DataTable
          v-else
          :value="detail.transactions"
          data-key="id"
          size="small"
          class="ledger"
          paginator
          :rows="15"
          :rows-per-page-options="[15, 30, 100]"
        >
          <Column field="date" header="Date">
            <template #body="{ data }">{{ formatDate(data.date) }}</template>
          </Column>
          <Column field="transaction_type" header="Type">
            <template #body="{ data }">{{ txnLabel(data.transaction_type) }}</template>
          </Column>
          <Column header="Units" class="num">
            <template #body="{ data }">{{ formatUnits(data.units) }}</template>
          </Column>
          <Column header="NAV / Price" class="num">
            <template #body="{ data }">{{ formatInrPaise(data.nav_or_price) }}</template>
          </Column>
          <Column header="Amount" class="num">
            <template #body="{ data }">{{ data.amount == null ? '—' : formatInr(data.amount) }}</template>
          </Column>
        </DataTable>
      </article>
    </template>
  </section>
</template>

<style scoped>
.scheme {
  padding: var(--fm-space-6);
  max-width: var(--fm-content-max);
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-5);
}

.page-head {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-2);
}
.back {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  background: none;
  border: none;
  padding: 0;
  font: inherit;
  color: var(--fm-text-muted);
  cursor: pointer;
}
.back:hover {
  color: var(--fm-verified);
}
.title-row {
  display: flex;
  align-items: center;
  gap: var(--fm-space-3);
  flex-wrap: wrap;
}
.title-row h1 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fm-space-2);
}
.chip {
  font-size: 0.75rem;
  font-weight: 500;
  padding: 0.2rem 0.55rem;
  border-radius: var(--fm-radius-pill);
  background: var(--fm-surface-raised);
  border: 1px solid var(--fm-border-subtle);
}
.chip.subtle {
  color: var(--fm-text-muted);
  font-variant-numeric: tabular-nums;
}
.nav-line {
  margin: 0;
  font-size: 0.9375rem;
}
.muted {
  color: var(--fm-text-muted);
}

.metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--fm-space-4);
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
.empty {
  padding: var(--fm-space-6) 0;
  text-align: center;
}

:deep(.ledger .num) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

@media (max-width: 768px) {
  .metrics {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
