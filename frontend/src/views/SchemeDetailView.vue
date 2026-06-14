<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Message from 'primevue/message'
import MetricCard from '@/components/MetricCard.vue'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import { useScheme } from '@/composables/useScheme'
import { useUiStore } from '@/stores/ui'
import { formatInr, formatInrPaise, formatUnits, formatDate } from '@/utils/format'

const NavHistoryChart = defineAsyncComponent(
  () => import('@/components/charts/NavHistoryChart.vue'),
)

const route = useRoute()
const router = useRouter()
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

// Surface *why* the XIRR is provisional, rather than a bare number.
const XIRR_STATUS_NOTES: Record<string, string> = {
  less_than_1_year: 'Held < 1 year — annualised, treat as indicative',
  estimated: 'Estimated — needs full transaction history',
}
const xirrStatusNote = computed(() => XIRR_STATUS_NOTES[detail.value?.xirr_status ?? ''] ?? '')

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

// Net-units sign rule mirrors folioman_core.fifo.net_units_from_transactions:
// inflows add, sells / transfers-out subtract, splits & dividends are unit-neutral.
const INFLOW = new Set(['buy', 'bonus', 'transfer_in'])
const OUTFLOW = new Set(['sell', 'transfer_out'])
function signedUnits(type: string, units: string | number | null | undefined): number {
  const u = num(units)
  if (INFLOW.has(type)) return u
  if (OUTFLOW.has(type)) return -u
  return 0
}

const TXN_FLOW: Record<string, 'in' | 'out' | 'neutral'> = {
  buy: 'in',
  bonus: 'in',
  transfer_in: 'in',
  sell: 'out',
  transfer_out: 'out',
  dividend: 'neutral',
  split: 'neutral',
}
function txnFlow(t: string): 'in' | 'out' | 'neutral' {
  return TXN_FLOW[t] ?? 'neutral'
}

// Running unit balance after each row, over the full chronological ledger (the
// backend orders by date,id asc); keyed by id so it survives table paging.
// Partial-history rows are skipped: without the missing opening balance their
// running total is meaningless, so they render "—" (see the Balance column).
const balanceById = computed<Record<number, number>>(() => {
  const map: Record<number, number> = {}
  let bal = 0
  for (const t of detail.value?.transactions ?? []) {
    if (!t.cost_basis_complete) continue
    bal += signedUnits(t.transaction_type, t.units)
    map[t.id] = bal
  }
  return map
})

// Buy/sell markers overlaid on the NAV line — what you paid vs the price then.
const navMarkers = computed(() =>
  (detail.value?.transactions ?? [])
    .filter(
      (t) =>
        (t.transaction_type === 'buy' || t.transaction_type === 'sell') && t.nav_or_price != null,
    )
    .map((t) => ({
      date: t.date,
      value: num(t.nav_or_price),
      type: t.transaction_type as 'buy' | 'sell',
    })),
)

// Held but unpriced: the value can't be computed because the latest NAV is stale.
const navStale = computed(() => {
  const d = detail.value
  return !!d && num(d.units) > 0 && d.value_inr == null
})

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
        <button class="back" type="button" @click="back">
          <i class="pi pi-arrow-left" /> Dashboard
        </button>
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
        >
          <small v-if="xirrStatusNote" class="metric-note">{{ xirrStatusNote }}</small>
        </MetricCard>
      </div>

      <article v-if="(detail.folios?.length ?? 0) > 1" class="card">
        <h2>Across folios</h2>
        <table class="folio-table">
          <thead>
            <tr>
              <th>Folio</th>
              <th class="num">Units</th>
              <th class="num">Value</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in detail.folios" :key="`${f.number}-${f.broker}`">
              <td>
                <span class="folio-num">{{ f.number }}</span>
                <small v-if="f.broker"> · {{ f.broker }}</small>
              </td>
              <td class="num">{{ formatUnits(f.units) }}</td>
              <td class="num">{{ f.value_inr == null ? '—' : formatInr(f.value_inr) }}</td>
            </tr>
          </tbody>
        </table>
      </article>

      <Message v-if="navStale" severity="warn" :closable="false">
        Current value is unavailable — the latest NAV on file is stale<span
          v-if="detail.latest_nav_date"
        >
          (last priced {{ formatDate(detail.latest_nav_date) }})</span
        >. Invested cost still shows.
      </Message>

      <article ref="chartRegion" class="card">
        <h2>NAV history</h2>
        <NavHistoryChart
          v-if="loadCharts && navSeries.length"
          :data="navSeries"
          :markers="navMarkers"
        />
        <div
          v-else-if="navSeries.length"
          class="chart-placeholder nav-placeholder"
          aria-hidden="true"
        />
        <p v-else class="muted empty">No NAV history on file for this scheme yet.</p>
      </article>

      <article class="card">
        <h2>Transactions</h2>
        <Message v-if="!detail.has_transactions" severity="info" :closable="false">
          Snapshot only — this holding has no transaction history, so there's no cost basis or
          capital-gains worksheet. Upload a <strong>since-inception (Detailed) CAS</strong> — or add
          the transactions manually — and we'll build them.
        </Message>
        <template v-else>
          <Message v-if="detail.partial_history" severity="warn" :closable="false">
            History before
            <strong>{{
              detail.partial_history_from
                ? formatDate(detail.partial_history_from)
                : 'this statement'
            }}</strong>
            is missing, so cost basis and capital gains aren't computed for this scheme — its value
            uses the statement's reported balance. The partial-statement rows are shown below,
            marked <span class="partial-pill">partial</span>. Import a
            <strong>since-inception (Detailed) CAS</strong> to enable gains.
          </Message>
          <DataTable
            v-if="loadCharts"
            :value="detail.transactions"
            data-key="id"
            size="small"
            class="ledger"
            paginator
            :rows="15"
            :rows-per-page-options="[15, 30, 100]"
            sort-field="date"
            :sort-order="-1"
          >
            <Column field="date" header="Date" sortable>
              <template #body="{ data }">
                {{ formatDate(data.date) }}
                <span v-if="!data.cost_basis_complete" class="partial-pill">partial</span>
              </template>
            </Column>
            <Column field="transaction_type" header="Type">
              <template #body="{ data }">
                <span class="flow" :class="txnFlow(data.transaction_type)">{{
                  txnLabel(data.transaction_type)
                }}</span>
              </template>
            </Column>
            <Column header="Units" class="num">
              <template #body="{ data }">{{ formatUnits(data.units) }}</template>
            </Column>
            <Column header="NAV / Price" class="num">
              <template #body="{ data }">{{ formatInrPaise(data.nav_or_price) }}</template>
            </Column>
            <Column header="Amount" class="num">
              <template #body="{ data }">{{
                data.amount == null ? '—' : formatInr(data.amount)
              }}</template>
            </Column>
            <Column header="Balance" class="num">
              <template #body="{ data }">{{
                data.cost_basis_complete ? formatUnits(balanceById[data.id]) : '—'
              }}</template>
            </Column>
          </DataTable>
          <div v-else class="table-placeholder" aria-hidden="true" />
        </template>
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
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--fm-space-4);
}
.metric-note {
  display: block;
  margin-top: var(--fm-space-1);
  font-size: 0.6875rem;
  color: var(--fm-text-muted);
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
.chart-placeholder {
  width: 100%;
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
.nav-placeholder {
  height: 260px;
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
.empty {
  padding: var(--fm-space-6) 0;
  text-align: center;
}

:deep(.ledger .num) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.folio-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}
.folio-table th {
  text-align: left;
  font-weight: 500;
  color: var(--fm-text-muted);
  padding: 0 0.5rem 0.5rem;
  border-bottom: 1px solid var(--fm-border-subtle);
}
.folio-table td {
  padding: 0.5rem;
  border-bottom: 1px solid var(--fm-border-subtle);
}
.folio-table tr:last-child td {
  border-bottom: none;
}
.folio-table .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.folio-table .folio-num {
  font-variant-numeric: tabular-nums;
}
.folio-table small {
  color: var(--fm-text-subtle);
}

/* Direction of each transaction at a glance: inflow vs outflow. */
.flow {
  font-size: 0.75rem;
  font-weight: 600;
}
.flow.in {
  color: var(--fm-gain);
}
.flow.out {
  color: var(--fm-loss);
}
.flow.neutral {
  color: var(--fm-text-muted);
}
/* Marks a partial-history row (kept for display, excluded from cost basis). */
.partial-pill {
  display: inline-block;
  margin-left: 0.35rem;
  padding: 0.05rem 0.4rem;
  font-size: 0.625rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--p-amber-600, #d97706);
  background: color-mix(in srgb, var(--p-amber-500, #f59e0b) 16%, transparent);
  border-radius: 999px;
  vertical-align: middle;
}
/* On a narrow screen the ledger scrolls within its card rather than widening
   the page. */
:deep(.ledger .p-datatable-table-container) {
  overflow-x: auto;
}

@media (max-width: 768px) {
  .metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
/* Phone: the four metric tiles stack to a single column. */
@media (max-width: 480px) {
  .metrics {
    grid-template-columns: minmax(0, 1fr);
  }
}
/* Phone: trim the chrome so content keeps the width. */
@media (max-width: 640px) {
  .scheme {
    padding: var(--fm-space-4);
  }
  .card {
    padding: var(--fm-space-4);
  }
}
</style>
