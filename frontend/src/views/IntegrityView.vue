<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Message from 'primevue/message'
import SelectButton from 'primevue/selectbutton'
import { useConfirm } from 'primevue/useconfirm'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import {
  hasIncompleteHistory,
  incompleteHistoryFix,
  incompleteHistoryReason,
  remediation,
} from '@/integrity/status'
import { useIntegrityStore, type IntegrityRow } from '@/stores/integrity'
import { useRosterStore } from '@/stores/roster'
import { useUiStore } from '@/stores/ui'
import { useWriteLock } from '@/composables/useWriteLock'
import { formatDate, formatUnits, toNumber } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const integrity = useIntegrityStore()
const roster = useRosterStore()
const ui = useUiStore()
const confirm = useConfirm()
const { readOnly } = useWriteLock()

const investorId = computed(() => {
  const raw = route.params.investorId
  const n = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(n) ? n : (ui.selectedInvestorId ?? 0)
})
const investorName = computed(() => roster.investorName(investorId.value) ?? 'Investor')

watch(investorId, (id) => void integrity.load(id), { immediate: true })

const rows = computed<IntegrityRow[]>(() => integrity.rowsFor(investorId.value))
const rollup = computed(() => integrity.rollupFor(investorId.value))
const showShimmer = computed(() => integrity.loading && rows.value.length === 0)

type FilterKey = 'all' | 'ready' | 'snapshot' | 'mismatch'
const filter = ref<FilterKey>('all')
const filters: { label: string; value: FilterKey }[] = [
  { label: 'All', value: 'all' },
  { label: 'Tax-ready', value: 'ready' },
  { label: 'Snapshots', value: 'snapshot' },
  { label: 'Needs attention', value: 'mismatch' },
]

const visibleRows = computed<IntegrityRow[]>(() => {
  switch (filter.value) {
    case 'ready':
      return rows.value.filter((r) => r.taxSafe)
    case 'snapshot':
      return rows.value.filter((r) => r.status === 'snapshot_only')
    case 'mismatch':
      return rows.value.filter((r) => r.status === 'mismatch')
    default:
      return rows.value
  }
})

interface SecurityGroup {
  securityId: number
  name: string
  isin: string
  rows: IntegrityRow[]
}

// One section per security, folios listed under it — a scheme can sit in several
// folios/demat accounts and reconcile differently in each.
const groups = computed<SecurityGroup[]>(() => {
  const byId = new Map<number, SecurityGroup>()
  for (const r of visibleRows.value) {
    let g = byId.get(r.securityId)
    if (!g) {
      g = { securityId: r.securityId, name: r.name, isin: r.isin, rows: [] }
      byId.set(r.securityId, g)
    }
    g.rows.push(r)
  }
  return [...byId.values()]
})

// Delta = snapshot-observed units − ledger units. Non-zero is the mismatch.
function delta(row: IntegrityRow): number | null {
  if (row.unitsFromHoldings == null || row.unitsFromTransactions == null) return null
  return toNumber(row.unitsFromHoldings) - toNumber(row.unitsFromTransactions)
}
function deltaLabel(row: IntegrityRow): string {
  const d = delta(row)
  if (d == null) return '—'
  return formatUnits(Math.abs(d))
}

// The always-visible explanation under each row: what the evidence says, in words.
function reasonFor(row: IntegrityRow): string {
  const incomplete = incompleteHistoryReason(row.issues)
  if (incomplete) return incomplete
  const ledger = formatUnits(row.unitsFromTransactions)
  const snap = formatUnits(row.unitsFromHoldings)
  const through = row.ledgerThrough ? ` through ${formatDate(row.ledgerThrough)}` : ''
  const asOf = row.snapshotAsOf ? formatDate(row.snapshotAsOf) : 'the latest statement'
  switch (row.status) {
    case 'full_history':
      return `Full transaction history${through} — every unit is accounted for.`
    case 'reconciled':
      return `Ledger of ${ledger} units matches the ${asOf} holdings.`
    case 'snapshot_only':
      return row.snapshotAsOf
        ? `Net worth tracked from the ${asOf} snapshot — no transaction history on file.`
        : 'Net worth tracked from a statement snapshot — no transaction history on file.'
    case 'mismatch':
      return `Ledger has ${ledger} units${through}; the ${asOf} holdings show ${snap} — off by ${deltaLabel(row)}.`
    case 'user_acknowledged':
      return `Known gap of ${deltaLabel(row)} units — kept out of the capital-gains worksheet.`
    default:
      return 'Integrity not yet computed for this holding.'
  }
}
function fixFor(row: IntegrityRow): string | null {
  if (hasIncompleteHistory(row.issues)) return incompleteHistoryFix()
  return remediation(row.status, { folioType: row.folioType })
}

const lastChecked = computed(() => {
  const stamps = rows.value.map((r) => r.lastReconciledAt).filter(Boolean) as string[]
  if (!stamps.length) return null
  return stamps.reduce((a, b) => (a > b ? a : b))
})

const recomputing = ref(false)
async function recheck(): Promise<void> {
  recomputing.value = true
  try {
    await integrity.recompute(investorId.value)
    ui.notify({ severity: 'success', summary: 'Integrity re-checked' })
  } finally {
    recomputing.value = false
  }
}

function askAcknowledge(row: IntegrityRow): void {
  confirm.require({
    header: 'Acknowledge this gap?',
    message:
      `Mark the reconcile gap on "${row.name}" as known. ` +
      'It stays out of the capital-gains worksheet — this dismisses the flag, it does not fix the units. ' +
      'To actually resolve it, re-import a since-inception CAS.',
    icon: 'pi pi-minus-circle',
    acceptLabel: 'Acknowledge',
    rejectLabel: 'Cancel',
    accept: async () => {
      const ok = await integrity.acknowledge(investorId.value, row.securityId, row.folioId)
      ui.notify(
        ok
          ? { severity: 'success', summary: 'Gap acknowledged' }
          : { severity: 'error', summary: 'Could not acknowledge', detail: integrity.error ?? '' },
      )
    },
  })
}

async function unacknowledge(row: IntegrityRow): Promise<void> {
  const ok = await integrity.unacknowledge(investorId.value, row.securityId, row.folioId)
  ui.notify(
    ok
      ? {
          severity: 'success',
          summary: 'Acknowledgement removed',
          detail: 'The gap is tracked again.',
        }
      : { severity: 'error', summary: 'Could not undo', detail: integrity.error ?? '' },
  )
}

function openScheme(securityId: number): void {
  void router.push({ name: 'scheme-detail', params: { investorId: investorId.value, securityId } })
}
function back(): void {
  void router.push({ name: 'dashboard', params: { investorId: investorId.value } })
}
</script>

<template>
  <section class="integrity-page">
    <header class="page-head">
      <button class="back" type="button" @click="back">
        <i class="pi pi-arrow-left" /> Dashboard
      </button>
      <div class="title-row">
        <h1>Data integrity</h1>
        <Button
          label="Re-check"
          icon="pi pi-refresh"
          size="small"
          outlined
          :loading="recomputing"
          :disabled="readOnly"
          @click="recheck"
        />
      </div>
      <p class="subtitle">
        {{ investorName }} — {{ rollup.taxReady }} of {{ rollup.total }} holdings tax-ready.
        <span v-if="lastChecked" class="muted">Last checked {{ formatDate(lastChecked) }}.</span>
      </p>
    </header>

    <Message
      v-if="rollup.mismatch || rollup.snapshot"
      severity="info"
      :closable="false"
      class="guidance"
    >
      <strong>How to resolve:</strong> a holding ties out once it has full transaction history.
      Re-import a <em>since-inception (Detailed) CAS</em> to close a gap or mismatch. You can also
      <em>acknowledge</em> a mismatch to mark it as known — it stays out of the worksheet either
      way.
    </Message>

    <div class="toolbar">
      <SelectButton
        v-model="filter"
        :options="filters"
        option-label="label"
        option-value="value"
        :allow-empty="false"
        size="small"
        aria-label="Filter by integrity status"
      />
    </div>

    <!-- Loading shimmer: a couple of group sketches while the first load runs. -->
    <div
      v-if="showShimmer"
      class="integrity-skeleton"
      aria-label="Checking integrity"
      aria-busy="true"
    >
      <div v-for="n in 3" :key="n" class="skel-group">
        <span class="fm-skeleton skel-head" />
        <span class="fm-skeleton skel-row" />
        <span class="fm-skeleton skel-detail" />
      </div>
    </div>

    <p v-else-if="!groups.length" class="empty">
      <i class="pi pi-inbox" />
      No holdings to reconcile yet. Import a CAS to get started.
    </p>

    <div v-else class="groups">
      <section v-for="g in groups" :key="g.securityId" class="sec-group">
        <header class="sec-head">
          <button class="sec-name" type="button" @click="openScheme(g.securityId)">
            {{ g.name }}
          </button>
          <small v-if="g.isin">{{ g.isin }}</small>
        </header>

        <ul class="folio-rows">
          <li v-for="row in g.rows" :key="row.folioId" class="folio-row" :class="row.status">
            <div class="row-main">
              <div class="folio-id">
                <span class="folio-num">{{ row.folioNumber || '—' }}</span>
                <small v-if="row.broker">{{ row.broker }}</small>
              </div>
              <IntegrityBadge
                :status="row.status"
                :label="hasIncompleteHistory(row.issues) ? 'Incomplete history' : undefined"
                :severity="hasIncompleteHistory(row.issues) ? 'warn' : undefined"
                size="sm"
              />
              <div class="row-action">
                <Button
                  v-if="row.status === 'mismatch'"
                  label="Acknowledge"
                  icon="pi pi-minus-circle"
                  size="small"
                  text
                  :loading="integrity.acknowledging"
                  :disabled="readOnly"
                  @click="askAcknowledge(row)"
                />
                <Button
                  v-else-if="row.status === 'user_acknowledged'"
                  label="Un-acknowledge"
                  icon="pi pi-undo"
                  size="small"
                  text
                  severity="secondary"
                  :loading="integrity.acknowledging"
                  :disabled="readOnly"
                  @click="unacknowledge(row)"
                />
              </div>
            </div>
            <p class="row-detail">
              <span class="reason">{{ reasonFor(row) }}</span>
              <span v-if="fixFor(row)" class="fix">{{ fixFor(row) }}</span>
            </p>
          </li>
        </ul>
      </section>
    </div>
  </section>
</template>

<style scoped>
.integrity-page {
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

.title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fm-space-3);
}
.title-row h1 {
  margin: 0;
}

.subtitle {
  margin: 0;
  color: var(--fm-text-muted);
  font-size: 0.9375rem;
}
.subtitle .muted {
  color: var(--fm-text-subtle);
}

.guidance {
  font-size: 0.875rem;
}

.toolbar {
  display: flex;
}

.groups {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-4);
}

.sec-group {
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-lg);
  overflow: hidden;
  background: var(--fm-surface);
}

.sec-head {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  padding: var(--fm-space-3) var(--fm-space-4);
  background: var(--fm-surface-raised);
  border-bottom: 1px solid var(--fm-border-subtle);
}
.sec-name {
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  font-weight: 600;
  font-size: 0.9375rem;
  color: var(--fm-text);
  text-align: left;
}
.sec-name:hover {
  color: var(--p-primary-color);
  text-decoration: underline;
}
.sec-head small {
  color: var(--fm-text-subtle);
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
}

.folio-rows {
  list-style: none;
  margin: 0;
  padding: 0;
}
.folio-row {
  padding: var(--fm-space-3) var(--fm-space-4);
  border-top: 1px solid var(--fm-border-subtle);
}
.folio-row:first-child {
  border-top: none;
}
/* A faint left accent keys the row to its severity without shouting. */
.folio-row.mismatch {
  box-shadow: inset 3px 0 0 var(--fm-critical);
}
.folio-row.snapshot_only {
  box-shadow: inset 3px 0 0 var(--fm-warn);
}

.row-main {
  display: flex;
  align-items: center;
  gap: var(--fm-space-3);
}
.folio-id {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  min-width: 0;
  flex: 1;
}
.folio-num {
  font-variant-numeric: tabular-nums;
  font-size: 0.875rem;
  color: var(--fm-text);
}
.folio-id small {
  color: var(--fm-text-subtle);
  font-size: 0.75rem;
}
.row-action {
  margin-left: auto;
}

.row-detail {
  margin: 0.4rem 0 0;
  font-size: 0.8125rem;
  line-height: 1.45;
  color: var(--fm-text-muted);
}
.row-detail .reason {
  font-variant-numeric: tabular-nums;
}
.row-detail .fix {
  display: block;
  margin-top: 0.15rem;
  color: var(--fm-text-subtle);
}

.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  margin: 0;
  padding: var(--fm-space-6);
  text-align: center;
  color: var(--fm-text-muted);
}
.empty .pi {
  font-size: 1.5rem;
  color: var(--fm-text-subtle);
}

/* Loading shimmer that sketches the grouped sections. */
.integrity-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-4);
}
.skel-group {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  padding: var(--fm-space-4);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-lg);
}
.skel-head {
  height: 1rem;
  width: 40%;
}
.skel-row {
  height: 1.25rem;
  width: 60%;
}
.skel-detail {
  height: 0.75rem;
  width: 85%;
}
</style>
