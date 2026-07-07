<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Message from 'primevue/message'
import Select from 'primevue/select'
import SelectButton from 'primevue/selectbutton'
import FyBarChart, { type FyBarPoint, type FyBarSeries } from '@/components/charts/FyBarChart.vue'
import { useIncome, type IncomeKindGroup, type IncomeRow } from '@/composables/useIncome'
import { useChartTokens } from '@/charts/useChartTokens'
import { useRosterStore } from '@/stores/roster'
import { useUiStore } from '@/stores/ui'
import { api } from '@/api/client'
import { downloadText } from '@/utils/csv'
import { formatInr } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const roster = useRosterStore()
const ui = useUiStore()
const tokens = useChartTokens()

const investorId = computed(() => {
  const raw = route.params.investorId
  const n = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(n) ? n : (ui.selectedInvestorId ?? 0)
})
const investorName = computed(() => roster.investorName(investorId.value) ?? 'Investor')

const {
  fy,
  fyOptions,
  kind,
  basis,
  byFy,
  loading,
  error,
  built,
  builtAt,
  visibleGroups,
  dividendsTotal,
  interestTotal,
  shownTotal,
  rowAmount,
  groupTotal,
  isInterest,
  isIncomplete,
  currentFy,
  build,
  loadSeries,
} = useIncome(investorId)

const kindOptions = [
  { label: 'Both', value: 'both' },
  { label: 'Dividends', value: 'dividend' },
  { label: 'Interest', value: 'interest' },
]
const basisOptions = [
  { label: 'Accrued', value: 'accrued' },
  { label: 'Received', value: 'received' },
]
// Basis matters only for interest (accrual vs payout); dividends are received-only,
// so hide the toggle when the view is dividends-only.
const showBasis = computed(() => kind.value !== 'dividend')

// FY drives only the breakdown table; kind/basis are pure view state. The
// year-over-year chart is FY-independent, so it loads once per investor.
watch(investorId, () => void loadSeries(), { immediate: true })
watch([investorId, fy], () => void build(), { immediate: true })

const ASSET_LABELS: Record<string, string> = {
  equity: 'Equity',
  etf: 'ETF',
  mf: 'Mutual fund',
  bond: 'Bond',
  fd: 'FD',
  crypto: 'Crypto',
  foreign_equity: 'Foreign equity',
}
function assetLabel(assetType: string): string {
  return ASSET_LABELS[assetType] ?? assetType
}

// Stacked-bar series (dividends + interest) across the years with data.
const chartSeries = computed<FyBarSeries[]>(() => [
  { key: 'dividends', label: 'Dividends', color: tokens.value.assetPalette[0] },
  { key: 'interest', label: 'Interest', color: tokens.value.assetPalette[1] },
])
const chartPoints = computed<FyBarPoint[]>(() =>
  byFy.value.map((p) => ({
    fy: p.fy,
    values: { dividends: Number(p.dividends), interest: Number(p.interest) },
  })),
)
const hasChart = computed(() => chartPoints.value.length > 1)

function selectFy(year: string): void {
  fy.value = year
}

async function download(): Promise<void> {
  const res = await api.GET('/api/investors/{investor_id}/reports/income.csv', {
    params: {
      path: { investor_id: investorId.value },
      query: { fy: fy.value, basis: basis.value },
    },
  })
  if (res.error || res.data == null) {
    ui.notify({ severity: 'error', summary: 'Couldn’t export income' })
    return
  }
  const saved = await downloadText(`income-${fy.value}.csv`, res.data as unknown as string)
  if (saved !== false) {
    ui.notify({ severity: 'success', summary: 'Income exported for your review' })
  }
}

function openScheme(row: IncomeRow): void {
  void router.push({
    name: 'scheme-detail',
    params: { investorId: investorId.value, securityId: row.security_id },
  })
}

function basisWord(group: IncomeKindGroup): string {
  return group.basis === 'received' ? 'received' : 'accrued'
}
</script>

<template>
  <section class="income-page">
    <header class="page-head">
      <button
        class="back"
        type="button"
        @click="router.push({ name: 'dashboard', params: { investorId } })"
      >
        <i class="pi pi-arrow-left" /> Dashboard
      </button>
      <h1>Income</h1>
      <p class="subtitle">
        {{ investorName }} — recurring income for the year (ITR Schedule OS), for your review.
      </p>
    </header>

    <Message severity="info" :closable="false" class="coming-soon">
      Showing <strong>dividends</strong> attributed to your holdings. FD and bond interest join this
      report once multi-asset support lands.
    </Message>

    <div class="controls">
      <label class="field">
        <span>Financial year</span>
        <Select v-model="fy" :options="fyOptions" :disabled="loading" size="small" />
      </label>
      <label class="field">
        <span>Show</span>
        <SelectButton
          v-model="kind"
          :options="kindOptions"
          option-label="label"
          option-value="value"
          :allow-empty="false"
          size="small"
        />
      </label>
      <label v-if="showBasis" class="field">
        <span>Basis</span>
        <SelectButton
          v-model="basis"
          :options="basisOptions"
          option-label="label"
          option-value="value"
          :allow-empty="false"
          size="small"
        />
      </label>
      <Button
        class="export-btn"
        label="Export CSV"
        icon="pi pi-download"
        size="small"
        outlined
        :disabled="loading || !built"
        @click="download"
      />
    </div>

    <Message v-if="error" severity="error" :closable="false">
      Couldn’t compute income for {{ fy }}. {{ error }}
    </Message>

    <section v-if="hasChart" class="chart-card">
      <h2>Income by year</h2>
      <FyBarChart
        :points="chartPoints"
        :series="chartSeries"
        :current-fy="currentFy"
        @select="selectFy"
      />
    </section>

    <template v-if="built && !loading">
      <section class="totals">
        <div class="stat">
          <span class="label"
            >Total ({{
              kind === 'both' ? 'all' : kindOptions.find((k) => k.value === kind)?.label
            }})</span
          >
          <span class="amount">{{ formatInr(shownTotal) }}</span>
        </div>
        <div class="stat">
          <span class="label">Dividends</span>
          <span class="amount">{{ formatInr(dividendsTotal) }}</span>
        </div>
        <div class="stat muted">
          <span class="label">Interest</span>
          <span class="amount">{{ formatInr(interestTotal) }}</span>
          <small>Coming with multi-asset</small>
        </div>
      </section>

      <section v-if="visibleGroups.length" class="breakdown">
        <article v-for="group in visibleGroups" :key="group.kind" class="kind-section">
          <header class="kind-head">
            <h2>{{ isInterest(group) ? 'Interest' : 'Dividends' }}</h2>
            <span class="basis-chip">{{ basisWord(group) }}</span>
            <span class="subtotal">{{ formatInr(groupTotal(group)) }}</span>
          </header>
          <ul class="rows">
            <li v-for="row in group.rows" :key="row.security_id" class="row">
              <button class="sec-name" type="button" @click="openScheme(row)">
                {{ row.name }}
              </button>
              <i
                v-if="isIncomplete(row.security_id)"
                v-tooltip.top="
                  'History not fully reconciled — dividends here may be understated. Check the Integrity page.'
                "
                class="pi pi-exclamation-triangle incomplete-flag"
                aria-label="History not fully reconciled"
              />
              <span class="asset-chip">{{ assetLabel(row.asset_type) }}</span>
              <span class="row-amount">{{ formatInr(rowAmount(row)) }}</span>
            </li>
          </ul>
        </article>

        <div class="grand-total">
          <span>Total income shown</span>
          <strong>{{ formatInr(shownTotal) }}</strong>
        </div>
      </section>

      <Message v-else severity="secondary" :closable="false" class="empty">
        No {{ kind === 'interest' ? 'interest' : 'dividend' }} income recorded for {{ fy }}.
      </Message>

      <p v-if="builtAt" class="built-at">
        Computed
        {{
          builtAt.toLocaleString(undefined, {
            day: 'numeric',
            month: 'short',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
          })
        }}.
      </p>
    </template>

    <div v-else-if="loading" class="income-skeleton" aria-busy="true">
      <div class="skel-cards">
        <span class="fm-skeleton skel-card" />
        <span class="fm-skeleton skel-card" />
        <span class="fm-skeleton skel-card" />
      </div>
      <span class="fm-skeleton skel-chart" />
      <span v-for="n in 4" :key="n" class="fm-skeleton skel-row" />
    </div>
  </section>
</template>

<style scoped>
.income-page {
  padding: var(--fm-space-6);
  max-width: var(--fm-content-max);
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-4);
  min-width: 0;
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
  color: var(--fm-text-muted);
  cursor: pointer;
  font-size: 0.875rem;
}
.back:hover {
  color: var(--fm-text);
}
.page-head h1 {
  margin: 0;
}
.subtitle {
  margin: 0;
  color: var(--fm-text-muted);
  font-size: 0.9375rem;
}
.coming-soon {
  font-size: 0.875rem;
}

.controls {
  display: flex;
  align-items: flex-end;
  gap: var(--fm-space-5);
  flex-wrap: wrap;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.export-btn {
  margin-left: auto;
}

.totals {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--fm-space-3);
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: var(--fm-space-4);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
  background: var(--fm-surface);
}
.stat .label {
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.stat .amount {
  font-size: 1.35rem;
  font-weight: 700;
}
.stat.muted {
  opacity: 0.7;
}
.stat.muted small {
  font-size: 0.6875rem;
  color: var(--fm-text-subtle);
}

.chart-card {
  padding: var(--fm-space-5);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
  background: var(--fm-surface);
}
.chart-card h2 {
  margin: 0 0 var(--fm-space-3);
  font-size: 1.05rem;
}

.breakdown {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-4);
}
.kind-section {
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
  background: var(--fm-surface);
  overflow: hidden;
}
.kind-head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: var(--fm-space-3) var(--fm-space-4);
  border-bottom: 1px solid var(--fm-border-subtle);
}
.kind-head h2 {
  margin: 0;
  font-size: 1rem;
}
.basis-chip {
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--fm-text-muted);
  background: var(--fm-surface-raised);
  padding: 0.1rem 0.45rem;
  border-radius: var(--fm-radius-pill);
}
.subtotal {
  margin-left: auto;
  font-weight: 700;
}
.rows {
  list-style: none;
  margin: 0;
  padding: 0;
}
.row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: var(--fm-space-3) var(--fm-space-4);
  border-bottom: 1px solid var(--fm-border-subtle);
}
.row:last-child {
  border-bottom: none;
}
.sec-name {
  background: none;
  border: none;
  padding: 0;
  color: var(--p-primary-color);
  cursor: pointer;
  font-size: 0.9375rem;
  text-align: left;
}
.sec-name:hover {
  text-decoration: underline;
}
.incomplete-flag {
  color: var(--fm-warn);
  font-size: 0.8rem;
  cursor: help;
}
.asset-chip {
  font-size: 0.6875rem;
  color: var(--fm-text-muted);
  background: var(--fm-surface-raised);
  padding: 0.1rem 0.45rem;
  border-radius: var(--fm-radius-pill);
}
.row-amount {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.grand-total {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--fm-space-4);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
  background: var(--fm-surface-raised);
  font-size: 1.05rem;
}
.empty {
  font-size: 0.875rem;
}
.built-at {
  margin: 0;
  font-size: 0.75rem;
  color: var(--fm-text-subtle);
}

.income-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-3);
}
.skel-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--fm-space-3);
}
.skel-card {
  height: 4.5rem;
}
.skel-chart {
  height: 16rem;
}
.skel-row {
  height: 2.5rem;
}
@media (max-width: 600px) {
  .totals,
  .skel-cards {
    grid-template-columns: 1fr;
  }
  .export-btn {
    margin-left: 0;
  }
}
</style>
