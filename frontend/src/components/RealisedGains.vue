<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import Column from 'primevue/column'
import ColumnGroup from 'primevue/columngroup'
import DataTable from 'primevue/datatable'
import Message from 'primevue/message'
import Row from 'primevue/row'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import type { CapitalGainRow, CapitalGains } from '@/composables/useCapitalGains'
import {
  integrityMeta,
  hasIncompleteHistory,
  incompleteHistoryFix,
  incompleteHistoryReason,
  remediation,
} from '@/integrity/status'
import type { IntegrityRow } from '@/stores/integrity'
import { formatDate, formatInr, formatUnits } from '@/utils/format'

const props = defineProps<{
  gains: CapitalGains | null
  excluded: IntegrityRow[]
  investorId: number
  builtAt?: Date | null
}>()

const router = useRouter()
const rows = computed(() => props.gains?.rows ?? [])
const stcg = computed(() => Number(props.gains?.stcg_total ?? 0))
const ltcg = computed(() => Number(props.gains?.ltcg_total ?? 0))

/** One summary row per security: consolidated units/sale/cost/gain, with the
 *  individual FIFO lots kept under `lots` for the expansion. */
interface SecuritySummary {
  key: string
  securityId: number | null
  name: string
  isin: string
  units: number
  saleValue: number
  cost: number
  gain: number
  long: boolean
  short: boolean
  grandfatheringGap: boolean
  lots: CapitalGainRow[]
}

const summaries = computed<SecuritySummary[]>(() => {
  const byKey = new Map<string, SecuritySummary>()
  for (const r of rows.value) {
    const key = r.isin || String(r.security_id ?? r.name)
    let s = byKey.get(key)
    if (!s) {
      s = {
        key,
        securityId: r.security_id,
        name: r.name,
        isin: r.isin,
        units: 0,
        saleValue: 0,
        cost: 0,
        gain: 0,
        long: false,
        short: false,
        grandfatheringGap: false,
        lots: [],
      }
      byKey.set(key, s)
    }
    s.units += Number(r.units)
    s.saleValue += Number(r.sale_value)
    s.cost += Number(r.cost)
    s.gain += Number(r.gain)
    s.long ||= r.term === 'long'
    s.short ||= r.term === 'short'
    s.grandfatheringGap ||= r.grandfathering_unavailable
    s.lots.push(r)
  }
  // Biggest absolute gain first — the rows a reviewer cares about most.
  return [...byKey.values()].sort((a, b) => Math.abs(b.gain) - Math.abs(a.gain))
})

const expandedRows = ref<Record<string, boolean>>({})

// Column tie-out totals (gain total should equal STCG + LTCG).
const totalSale = computed(() => rows.value.reduce((s, r) => s + Number(r.sale_value), 0))
const totalCost = computed(() => rows.value.reduce((s, r) => s + Number(r.cost), 0))
const totalGain = computed(() => rows.value.reduce((s, r) => s + Number(r.gain), 0))

// Disposals whose pre-2018 grandfathering FMV is missing — gain may be overstated.
const grandfatheringGaps = computed(() => rows.value.filter((r) => r.grandfathering_unavailable))

const integrityTo = computed(() => ({
  name: 'integrity',
  params: { investorId: props.investorId },
}))

const computedAt = computed(() =>
  props.builtAt
    ? props.builtAt.toLocaleString(undefined, {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : null,
)

function termLabel(s: SecuritySummary): string {
  if (s.long && s.short) return 'LTCG · STCG'
  return s.long ? 'LTCG' : 'STCG'
}
function termClass(s: SecuritySummary): string {
  if (s.long && s.short) return 'mixed'
  return s.long ? 'long' : 'short'
}

function openScheme(securityId: number | null): void {
  if (securityId == null) return
  void router.push({ name: 'scheme-detail', params: { investorId: props.investorId, securityId } })
}

// Approximate holding period for a sanity-check on the term classification.
function holdingPeriod(acquired: string, sold: string): string {
  const a = new Date(acquired)
  const b = new Date(sold)
  let months = (b.getFullYear() - a.getFullYear()) * 12 + (b.getMonth() - a.getMonth())
  if (b.getDate() < a.getDate()) months -= 1
  if (months < 1) {
    const days = Math.max(0, Math.round((b.getTime() - a.getTime()) / 86_400_000))
    return `${days}d`
  }
  const years = Math.floor(months / 12)
  const rem = months % 12
  if (years === 0) return `${rem}m`
  return rem === 0 ? `${years}y` : `${years}y ${rem}m`
}

// "Left out" rows reuse the shared integrity vocabulary so the wording matches
// the Data integrity screen exactly (incl. demat-inherent vs fixable snapshot).
function excludedReason(row: IntegrityRow): string {
  return incompleteHistoryReason(row.issues) ?? integrityMeta(row.status).tooltip
}
function excludedFix(row: IntegrityRow): string | null {
  if (hasIncompleteHistory(row.issues)) return incompleteHistoryFix()
  return remediation(row.status, { folioType: row.folioType })
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

    <Message v-if="grandfatheringGaps.length" severity="warn" :closable="false" class="gf-note">
      {{ grandfatheringGaps.length }} long-term
      {{ grandfatheringGaps.length === 1 ? 'disposal is' : 'disposals are' }} missing the 31 Jan
      2018 fair market value used for grandfathering, so their cost is understated and the gain (and
      any tax) may be <strong>overstated</strong>. Verify these before relying on the figure.
    </Message>

    <section class="included">
      <h2>
        Realised gains <span class="count">{{ summaries.length }}</span>
      </h2>
      <DataTable
        v-if="summaries.length"
        v-model:expanded-rows="expandedRows"
        :value="summaries"
        data-key="key"
        class="gains-table"
        size="small"
        :pt="{ table: { style: 'min-width: 48rem' } }"
      >
        <Column :expander="true" class="exp-col" />
        <Column header="Holding">
          <template #body="{ data }">
            <button class="holding-name" type="button" @click="openScheme(data.securityId)">
              <span>{{ data.name }}</span>
              <small
                >{{ data.isin }} · {{ data.lots.length }}
                {{ data.lots.length === 1 ? 'lot' : 'lots' }}</small
              >
            </button>
          </template>
        </Column>
        <Column header="Term">
          <template #body="{ data }">
            <span class="term" :class="termClass(data)">{{ termLabel(data) }}</span>
          </template>
        </Column>
        <Column header="Units" class="num">
          <template #body="{ data }">{{ formatUnits(data.units) }}</template>
        </Column>
        <Column header="Sale value" class="num">
          <template #body="{ data }">{{ formatInr(data.saleValue) }}</template>
        </Column>
        <Column header="Cost" class="num">
          <template #body="{ data }">{{ formatInr(data.cost) }}</template>
        </Column>
        <Column header="Gain" class="num">
          <template #body="{ data }">
            <span class="gain-cell">
              <i
                v-if="data.grandfatheringGap"
                class="pi pi-exclamation-triangle gf-flag"
                title="A lot is missing its pre-2018 grandfathering FMV — gain may be overstated"
              />
              <span :class="{ neg: data.gain < 0 }">{{ formatInr(data.gain) }}</span>
            </span>
          </template>
        </Column>

        <template #expansion="{ data }">
          <div class="lots">
            <DataTable :value="data.lots" size="small" class="lots-table">
              <Column header="Term">
                <template #body="{ data: lot }">
                  <span class="term" :class="lot.term">{{
                    lot.term === 'long' ? 'LTCG' : 'STCG'
                  }}</span>
                </template>
              </Column>
              <Column header="Held">
                <template #body="{ data: lot }">
                  <div class="period">
                    <span class="held">{{ holdingPeriod(lot.acquired_on, lot.sold_on) }}</span>
                    <small>{{ formatDate(lot.acquired_on) }} → {{ formatDate(lot.sold_on) }}</small>
                  </div>
                </template>
              </Column>
              <Column header="Units" class="num">
                <template #body="{ data: lot }">{{ formatUnits(lot.units) }}</template>
              </Column>
              <Column header="Sale value" class="num">
                <template #body="{ data: lot }">{{ formatInr(lot.sale_value) }}</template>
              </Column>
              <Column header="Cost" class="num">
                <template #body="{ data: lot }">{{ formatInr(lot.cost) }}</template>
              </Column>
              <Column header="Gain" class="num">
                <template #body="{ data: lot }">
                  <span class="gain-cell">
                    <i
                      v-if="lot.grandfathering_unavailable"
                      class="pi pi-exclamation-triangle gf-flag"
                      title="Pre-2018 grandfathering FMV missing — gain may be overstated"
                    />
                    <span :class="{ neg: Number(lot.gain) < 0 }">{{ formatInr(lot.gain) }}</span>
                  </span>
                </template>
              </Column>
            </DataTable>
          </div>
        </template>

        <ColumnGroup type="footer">
          <Row>
            <Column footer="Total" :colspan="4" footer-class="foot-label" />
            <Column :footer="formatInr(totalSale)" class="num" />
            <Column :footer="formatInr(totalCost)" class="num" />
            <Column :footer="formatInr(totalGain)" class="num" />
          </Row>
        </ColumnGroup>
      </DataTable>
      <p v-else class="empty">
        No realised gains in this year. Redeem a fully-reconciled holding (or pick another year) and
        they’ll appear here.
      </p>
      <p v-if="computedAt && summaries.length" class="freshness">
        Computed {{ computedAt }} from tax-ready folios.
      </p>
    </section>

    <section v-if="excluded.length" class="excluded">
      <header class="ex-head-row">
        <h2>
          Left out <span class="count">{{ excluded.length }}</span>
        </h2>
        <RouterLink :to="integrityTo" class="review-link">Review in Data integrity →</RouterLink>
      </header>
      <ul class="excluded-list">
        <li v-for="row in excluded" :key="`${row.securityId}-${row.folioId}`">
          <div class="ex-head">
            <span class="ex-name">{{ row.name }}</span>
            <IntegrityBadge :status="row.status" size="sm" />
          </div>
          <p class="ex-reason">
            {{ excludedReason(row) }}
            <span v-if="excludedFix(row)" class="ex-fix">{{ excludedFix(row) }}</span>
          </p>
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

.gf-note {
  font-size: 0.8125rem;
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
:deep(.exp-col) {
  width: 3rem;
}
:deep(.num) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
:deep(.foot-label) {
  text-align: right;
  color: var(--fm-text-muted);
  font-weight: 600;
}
:deep(tfoot td) {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

/* The per-lot detail table revealed when a security row expands. */
.lots {
  padding: var(--fm-space-2) var(--fm-space-3) var(--fm-space-3);
}
:deep(.lots-table) {
  font-size: 0.8125rem;
}

.period {
  display: flex;
  flex-direction: column;
  gap: 0.05rem;
}
.period .held {
  font-variant-numeric: tabular-nums;
  font-size: 0.8125rem;
}
.period small {
  color: var(--fm-text-subtle);
  font-size: 0.6875rem;
  white-space: nowrap;
}

.gain-cell {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  justify-content: flex-end;
}
.gf-flag {
  color: var(--fm-warn);
  font-size: 0.75rem;
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
.term.mixed {
  color: var(--fm-text-muted);
  background: var(--fm-surface-raised);
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
.freshness {
  margin: var(--fm-space-2) 0 0;
  font-size: 0.75rem;
  color: var(--fm-text-subtle);
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
.ex-fix {
  display: block;
  margin-top: 0.15rem;
  color: var(--fm-text-subtle);
}
</style>
