<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Message from 'primevue/message'
import SelectButton from 'primevue/selectbutton'
import { useConfirm } from 'primevue/useconfirm'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import { useIntegrityStore, type IntegrityRow } from '@/stores/integrity'
import { useRosterStore } from '@/stores/roster'
import { useUiStore } from '@/stores/ui'
import { formatDate, formatUnits, toNumber } from '@/utils/format'

const DataTable = defineAsyncComponent(() => import('primevue/datatable'))
const Column = defineAsyncComponent(() => import('primevue/column'))

const route = useRoute()
const router = useRouter()
const integrity = useIntegrityStore()
const roster = useRosterStore()
const ui = useUiStore()
const confirm = useConfirm()

const investorId = computed(() => {
  const raw = route.params.investorId
  const n = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(n) ? n : (ui.selectedInvestorId ?? 0)
})
const investorName = computed(() => roster.investorName(investorId.value) ?? 'Investor')

watch(investorId, (id) => void integrity.load(id), { immediate: true })

const rows = computed<IntegrityRow[]>(() => integrity.rowsFor(investorId.value))
const rollup = computed(() => integrity.rollupFor(investorId.value))

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

// Delta = snapshot-observed units − ledger units. Non-zero is what a mismatch is.
function delta(row: IntegrityRow): number | null {
  if (row.unitsFromHoldings == null || row.unitsFromTransactions == null) return null
  return toNumber(row.unitsFromHoldings) - toNumber(row.unitsFromTransactions)
}
function deltaLabel(row: IntegrityRow): string {
  const d = delta(row)
  if (d == null) return '—'
  const sign = d > 0 ? '+' : d < 0 ? '−' : ''
  return `${sign}${formatUnits(Math.abs(d))}`
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
      `Mark the mismatch on "${row.name}" as known. ` +
      'It stays out of the capital-gains worksheet — this dismisses the flag, it does not fix the units. ' +
      'To actually resolve it, re-import a since-inception CAS.',
    icon: 'pi pi-minus-circle',
    acceptLabel: 'Acknowledge',
    rejectLabel: 'Cancel',
    accept: async () => {
      const ok = await integrity.acknowledge(investorId.value, row.securityId, row.folioId)
      ui.notify(
        ok
          ? { severity: 'success', summary: 'Mismatch acknowledged' }
          : { severity: 'error', summary: 'Could not acknowledge', detail: integrity.error ?? '' },
      )
    },
  })
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
      <button class="back" type="button" @click="back"><i class="pi pi-arrow-left" /> Dashboard</button>
      <div class="title-row">
        <h1>Data integrity</h1>
        <Button
          label="Re-check"
          icon="pi pi-refresh"
          size="small"
          outlined
          :loading="recomputing"
          @click="recheck"
        />
      </div>
      <p class="subtitle">
        {{ investorName }} — {{ rollup.taxReady }} of {{ rollup.total }} holdings tax-ready.
        <span v-if="lastChecked" class="muted">Last checked {{ formatDate(lastChecked) }}.</span>
      </p>
    </header>

    <Message v-if="rollup.mismatch || rollup.snapshot" severity="info" :closable="false" class="guidance">
      <strong>How to resolve:</strong> a holding ties out once it has full transaction history.
      Re-import a <em>since-inception (Detailed) CAS</em> to close a gap or mismatch. You can also
      <em>acknowledge</em> a mismatch to mark it as known — it stays out of the worksheet either way.
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

    <DataTable
      :value="visibleRows"
      data-key="folioId"
      class="integrity-table"
      size="small"
      :pt="{ table: { style: 'min-width: 40rem' } }"
    >
      <template #empty>
        <p class="empty">No holdings to reconcile yet.</p>
      </template>

      <Column header="Holding">
        <template #body="{ data }">
          <button class="holding-name" type="button" @click="openScheme(data.securityId)">
            <span>{{ data.name }}</span>
            <small>{{ [data.folioNumber, data.broker].filter(Boolean).join(' · ') || data.isin }}</small>
          </button>
        </template>
      </Column>
      <Column header="Status">
        <template #body="{ data }">
          <IntegrityBadge :status="data.status" size="sm" />
        </template>
      </Column>
      <Column header="Ledger units" class="num">
        <template #body="{ data }">
          {{ data.unitsFromTransactions == null ? '—' : formatUnits(data.unitsFromTransactions) }}
        </template>
      </Column>
      <Column header="Snapshot units" class="num">
        <template #body="{ data }">
          {{ data.unitsFromHoldings == null ? '—' : formatUnits(data.unitsFromHoldings) }}
        </template>
      </Column>
      <Column header="Delta" class="num">
        <template #body="{ data }">
          <span :class="{ 'delta-off': delta(data) !== null && delta(data) !== 0 }">{{ deltaLabel(data) }}</span>
        </template>
      </Column>
      <Column header="" class="action-col">
        <template #body="{ data }">
          <Button
            v-if="data.status === 'mismatch'"
            label="Acknowledge"
            icon="pi pi-minus-circle"
            size="small"
            text
            :loading="integrity.acknowledging"
            @click="askAcknowledge(data)"
          />
        </template>
      </Column>
    </DataTable>
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

/* Let the table scroll within its own box on narrow screens rather than
   widening the page. */
.integrity-table {
  overflow-x: auto;
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

:deep(.num) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
:deep(.action-col) {
  text-align: right;
}

.delta-off {
  color: var(--fm-critical);
  font-weight: 600;
}

.empty {
  margin: 0;
  padding: var(--fm-space-4);
  text-align: center;
  color: var(--fm-text-muted);
}
</style>
