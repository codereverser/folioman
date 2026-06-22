<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Menu from 'primevue/menu'
import type { MenuItem } from 'primevue/menuitem'
import Message from 'primevue/message'
import Select from 'primevue/select'
import SelectButton from 'primevue/selectbutton'
import { useConfirm } from 'primevue/useconfirm'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import CorporateActionForm from '@/components/CorporateActionForm.vue'
import {
  corporateActionManualNote,
  corporateActionSuggestionSummary,
  corporateActionSuggestions,
  hasCorporateActionSuggestion,
  openingLotIssue,
  openingLotSummary,
  OPENING_LOT_CLASSIFICATIONS,
  hasIncompleteHistory,
  incompleteHistoryFix,
  incompleteHistoryReason,
  hasLedgerPositionNotInHoldings,
  ledgerPositionSummary,
  remediation,
  rowNeedsAttention,
  type CorporateActionSuggestion,
} from '@/integrity/status'
import { metaResolutions, workResolutions, type Resolution } from '@/integrity/resolutions'
import type { ManualCaKind } from '@/integrity/manualCorporateAction'
import {
  useIntegrityStore,
  type IntegrityRow,
  type ManualCorporateActionBody,
} from '@/stores/integrity'
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

watch(
  investorId,
  (id) => {
    void integrity.load(id)
    void integrity.loadSecurities(id)
  },
  { immediate: true },
)

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
      // Passive eCAS snapshots only — actionable ones (opening lot, etc.) sit under Needs attention.
      return rows.value.filter(
        (r) => r.status === 'snapshot_only' && !rowNeedsAttention(r.status, r.issues),
      )
    case 'mismatch':
      return rows.value.filter((r) => rowNeedsAttention(r.status, r.issues))
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
  if (hasLedgerPositionNotInHoldings(row.issues)) {
    return ledgerPositionSummary(row.name, row.unitsFromTransactions)
  }
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
  const lot = openingLotIssue(row.issues)
  if (lot) return openingLotSummary(lot)
  const caManual = corporateActionManualNote(row.issues)
  if (caManual) return caManual
  const suggestion = corporateActionSuggestions(row.issues)[0]
  if (suggestion) return corporateActionSuggestionSummary(suggestion)
  return remediation(row.status, { folioType: row.folioType })
}

function integrityBadgeLabel(row: IntegrityRow): string | undefined {
  if (hasIncompleteHistory(row.issues)) return 'Incomplete history'
  if (hasCorporateActionSuggestion(row.issues)) return 'Action suggested'
  if (hasLedgerPositionNotInHoldings(row.issues)) return 'Merger review'
  if (openingLotIssue(row.issues)) return 'Needs opening lot'
  return undefined
}

function integrityBadgeSeverity(row: IntegrityRow): 'warn' | undefined {
  if (
    hasIncompleteHistory(row.issues) ||
    hasCorporateActionSuggestion(row.issues) ||
    hasLedgerPositionNotInHoldings(row.issues) ||
    openingLotIssue(row.issues)
  ) {
    return 'warn'
  }
  return undefined
}

function suggestionFor(row: IntegrityRow): CorporateActionSuggestion | null {
  return corporateActionSuggestions(row.issues)[0] ?? null
}

// The inline preview table is the verification surface, so applying is a direct
// action — no second confirmation dialog repeating what the table already shows.
async function applyCorporateActionFor(row: IntegrityRow): Promise<void> {
  const suggestion = suggestionFor(row)
  if (!suggestion) return
  const ok = await integrity.applyCorporateAction(
    investorId.value,
    row.securityId,
    row.folioId,
    suggestion.referenceIds,
  )
  ui.notify(
    ok
      ? {
          severity: 'success',
          summary: 'Corporate action applied',
          detail: 'Ledger updated and folio re-reconciled.',
        }
      : {
          severity: 'error',
          summary: 'Could not apply',
          detail: integrity.error ?? '',
        },
  )
}

const openingLotVisible = ref(false)
const openingLotRow = ref<IntegrityRow | null>(null)
interface OpeningLotEntry {
  date: string
  units: string
  price: string
}
const openingLotForm = ref({
  classification: 'transfer_in',
  date: '',
  price: '',
  costBasisUnknown: false,
  // Multi-lot entry for a demerger receipt — one row per lot on the broker's breakdown.
  lots: [] as OpeningLotEntry[],
  // The demerger's ex-date — links the receipt to its parent and reduces the parent's
  // cost basis at that date. Optional: without it the lots record but stay unlinked.
  demergerDate: '',
})

// A demerger receipt arrives as several lots (the broker allocates a date + cost to
// each), so its entry is a multi-row grid; other classifications are a single lot.
const isMultiLot = computed(() => openingLotForm.value.classification === 'demerger_result')

function openOpeningLotDialog(row: IntegrityRow, classification: string = 'transfer_in'): void {
  openingLotRow.value = row
  openingLotForm.value = {
    classification,
    date: row.snapshotAsOf ?? '',
    price: '',
    costBasisUnknown: false,
    lots: classification === 'demerger_result' ? [{ date: '', units: '', price: '' }] : [],
    demergerDate: '',
  }
  openingLotVisible.value = true
}

function addOpeningLot(): void {
  openingLotForm.value.lots.push({ date: '', units: '', price: '' })
}

function removeOpeningLot(index: number): void {
  openingLotForm.value.lots.splice(index, 1)
}

const openingLotUnitsTotal = computed(() =>
  openingLotForm.value.lots.reduce((sum, l) => sum + (Number(l.units) || 0), 0),
)

const openingLotValid = computed(() => {
  if (isMultiLot.value) {
    return (
      openingLotForm.value.lots.length > 0 &&
      openingLotForm.value.lots.every((l) => l.date && Number(l.units) > 0)
    )
  }
  return !!openingLotForm.value.date
})

async function submitOpeningLot(): Promise<void> {
  const row = openingLotRow.value
  if (!row || !openingLotValid.value) return
  const ok = isMultiLot.value
    ? await integrity.recordOpeningLots(investorId.value, row.securityId, row.folioId, {
        classification: openingLotForm.value.classification,
        lots: openingLotForm.value.lots.map((l) => ({
          date: l.date,
          units: l.units,
          price: l.price || undefined,
        })),
        cost_basis_unknown: openingLotForm.value.costBasisUnknown,
        demerger_date: openingLotForm.value.demergerDate || undefined,
      })
    : await integrity.recordOpeningLot(investorId.value, row.securityId, row.folioId, {
        classification: openingLotForm.value.classification,
        date: openingLotForm.value.date,
        price: openingLotForm.value.price || undefined,
        cost_basis_unknown: openingLotForm.value.costBasisUnknown,
      })
  if (ok) {
    openingLotVisible.value = false
    openingLotRow.value = null
    ui.notify({ severity: 'success', summary: 'Opening lot recorded' })
  } else {
    ui.notify({
      severity: 'error',
      summary: 'Could not record opening lot',
      detail: integrity.error ?? '',
    })
  }
}

const identityRemapVisible = ref(false)
const identityRemapRow = ref<IntegrityRow | null>(null)
const identityRemapIsin = ref('')

function openIdentityRemapDialog(row: IntegrityRow): void {
  identityRemapRow.value = row
  identityRemapIsin.value = ''
  identityRemapVisible.value = true
}

async function submitIdentityRemap(): Promise<void> {
  const row = identityRemapRow.value
  const toIsin = identityRemapIsin.value.trim().toUpperCase()
  if (!row || !toIsin) return
  const ok = await integrity.applyIdentityRemap(investorId.value, row.securityId, row.folioId, {
    to_isin: toIsin,
  })
  if (ok) {
    identityRemapVisible.value = false
    identityRemapRow.value = null
    await integrity.load(investorId.value, { force: true })
    ui.notify({ severity: 'success', summary: 'Identity remapped' })
  } else {
    ui.notify({
      severity: 'error',
      summary: 'Could not remap identity',
      detail: integrity.error ?? '',
    })
  }
}

const manualCaVisible = ref(false)
const manualCaRow = ref<IntegrityRow | null>(null)
const manualCaInitialKind = ref<ManualCaKind | undefined>(undefined)
const manualCaVariant = ref<'general' | 'merger'>('general')

function openManualCaDialog(row: IntegrityRow, kind?: ManualCaKind): void {
  manualCaRow.value = row
  manualCaInitialKind.value = kind
  manualCaVariant.value =
    kind === 'merger' && hasLedgerPositionNotInHoldings(row.issues) ? 'merger' : 'general'
  manualCaVisible.value = true
}

async function submitManualCa(body: ManualCorporateActionBody): Promise<void> {
  const row = manualCaRow.value
  if (!row) return
  const ok = await integrity.applyManualCorporateAction(
    investorId.value,
    row.securityId,
    row.folioId,
    body,
  )
  if (ok) {
    manualCaVisible.value = false
    manualCaRow.value = null
    await integrity.load(investorId.value, { force: true })
    ui.notify({ severity: 'success', summary: 'Corporate action applied' })
  } else {
    ui.notify({
      severity: 'error',
      summary: 'Could not apply the corporate action',
      detail: integrity.error ?? '',
    })
  }
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

async function runResolution(row: IntegrityRow, resolution: Resolution): Promise<void> {
  switch (resolution.id) {
    case 'apply_ca_suggestion':
      await applyCorporateActionFor(row)
      break
    case 'opening_lot':
      openOpeningLotDialog(row, resolution.openingLotClassification ?? 'transfer_in')
      break
    case 'identity_remap':
      openIdentityRemapDialog(row)
      break
    case 'manual_ca':
      openManualCaDialog(row, resolution.manualCaKind)
      break
    case 'fetch_corporate_actions':
      await fetchCorporateActions()
      break
    case 'acknowledge':
      askAcknowledge(row)
      break
    case 'unacknowledge':
      await unacknowledge(row)
      break
  }
}

function resolutionLoading(resolution: Resolution): boolean {
  switch (resolution.id) {
    case 'apply_ca_suggestion':
      return integrity.applyingCorporateAction
    case 'opening_lot':
      return integrity.recordingOpeningLot
    case 'identity_remap':
      return integrity.applyingIdentityRemap
    case 'manual_ca':
      return integrity.applyingManualCorporateAction
    case 'fetch_corporate_actions':
      return integrity.refreshingCorporateActions
    case 'acknowledge':
    case 'unacknowledge':
      return integrity.acknowledging
    default:
      return false
  }
}

function rowResolveLoading(row: IntegrityRow): boolean {
  return workResolutions(row).some((r) => resolutionLoading(r))
}

const resolveMenu = ref<InstanceType<typeof Menu> | null>(null)
const resolveMenuRow = ref<IntegrityRow | null>(null)
const resolveMenuModel = ref<MenuItem[]>([])

function openResolveMenu(e: Event, row: IntegrityRow): void {
  resolveMenuRow.value = row
  resolveMenuModel.value = workResolutions(row).map((r) => ({
    label: r.label,
    icon: r.icon,
    disabled: readOnly.value,
    command: () => void runResolution(row, r),
  }))
  resolveMenu.value?.toggle(e)
}

// A mismatch whose corporate actions haven't been fetched yet: don't let the user
// hand-author one (they'd guess a single coarse action for what may be several
// separate events). Wait for the feed first.
function caPending(row: IntegrityRow): boolean {
  return row.status === 'mismatch' && !row.caSyncedAt && !suggestionFor(row)
}
const hasPendingCa = computed(() => rows.value.some(caPending))

async function fetchCorporateActions(): Promise<void> {
  const ok = await integrity.refreshCorporateActions(investorId.value)
  ui.notify(
    ok
      ? { severity: 'success', summary: 'Corporate actions fetched' }
      : {
          severity: 'error',
          summary: 'Could not fetch corporate actions',
          detail: integrity.error ?? '',
        },
  )
}

function askAcknowledge(row: IntegrityRow): void {
  confirm.require({
    header: 'Acknowledge this gap?',
    message:
      `Mark the reconcile gap on "${row.name}" as known. ` +
      'It stays out of the capital-gains worksheet — this dismisses the flag, it does not fix the units. ' +
      'To actually resolve it, re-import a since-inception CAS.',
    icon: 'pi pi-minus-circle',
    rejectProps: { label: 'Cancel', severity: 'secondary', outlined: true },
    acceptProps: { label: 'Acknowledge' },
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
          v-if="hasPendingCa"
          label="Fetch corporate actions"
          icon="pi pi-cloud-download"
          size="small"
          :loading="integrity.refreshingCorporateActions"
          :disabled="readOnly"
          @click="fetchCorporateActions"
        />
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
      <strong>How to resolve:</strong> a holding ties out once it has full transaction history. We
      fetch your stocks’ corporate actions in the background; if a mismatch is still open,
      <em>Fetch corporate actions</em> first, then apply the suggested split/bonus — or use
      <em>Resolve…</em> to pick how to fix it once the history is in. You can also
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
                :label="integrityBadgeLabel(row)"
                :severity="integrityBadgeSeverity(row)"
                size="sm"
              />
              <div class="row-action">
                <Button
                  v-if="workResolutions(row).length"
                  label="Resolve…"
                  icon="pi pi-wrench"
                  size="small"
                  :loading="rowResolveLoading(row)"
                  :disabled="readOnly"
                  aria-haspopup="true"
                  @click="openResolveMenu($event, row)"
                />
                <Button
                  v-for="(resolution, ri) in metaResolutions(row)"
                  :key="`${row.folioId}-${resolution.id}-${ri}`"
                  :label="resolution.label"
                  :icon="resolution.icon"
                  size="small"
                  text
                  :severity="resolution.id === 'unacknowledge' ? 'secondary' : undefined"
                  :loading="resolutionLoading(resolution)"
                  :disabled="readOnly"
                  @click="runResolution(row, resolution)"
                />
              </div>
            </div>
            <p class="row-detail">
              <span class="reason">{{ reasonFor(row) }}</span>
              <span v-if="fixFor(row)" class="fix">{{ fixFor(row) }}</span>
            </p>
            <div v-if="suggestionFor(row)" class="ca-preview">
              <p class="ca-preview-head">We’ll apply these on their original dates:</p>
              <table class="ca-table">
                <thead>
                  <tr>
                    <th>Ex-date</th>
                    <th>Action</th>
                    <th class="num">Shares before</th>
                    <th class="num">Shares after</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(e, i) in suggestionFor(row)!.events" :key="i">
                    <td>{{ formatDate(e.exDate) }}</td>
                    <td>{{ e.subject }}</td>
                    <td class="num">{{ e.unitsBefore }}</td>
                    <td class="num">{{ e.unitsAfter }}</td>
                  </tr>
                </tbody>
              </table>
              <p v-if="suggestionFor(row)!.partial" class="ca-preview-note">
                Clears the missing-buys gap; a residual difference from your holdings remains —
                likely a merger or transfer to record separately.
              </p>
            </div>
          </li>
        </ul>
      </section>
    </div>

    <Dialog
      v-model:visible="openingLotVisible"
      header="Record opening lot"
      modal
      :style="{ width: '28rem' }"
      @hide="openingLotRow = null"
    >
      <p v-if="openingLotRow && openingLotIssue(openingLotRow.issues)" class="dialog-copy">
        {{ openingLotSummary(openingLotIssue(openingLotRow.issues)!) }}
      </p>
      <div class="dialog-form">
        <label>
          Classification
          <Select
            v-model="openingLotForm.classification"
            :options="[...OPENING_LOT_CLASSIFICATIONS]"
            option-label="label"
            option-value="value"
          />
        </label>
        <template v-if="!isMultiLot">
          <label>
            Acquisition date
            <InputText v-model="openingLotForm.date" type="date" />
          </label>
          <label>
            Cost per unit (optional)
            <InputText
              v-model="openingLotForm.price"
              inputmode="decimal"
              :disabled="openingLotForm.costBasisUnknown"
            />
          </label>
        </template>
        <template v-else>
          <p class="dialog-copy">
            Enter one row per lot from your broker's holding breakdown — the inherited acquisition
            date, quantity, and allocated cost per unit.
          </p>
          <div v-for="(lot, i) in openingLotForm.lots" :key="i" class="lot-row">
            <InputText v-model="lot.date" type="date" :disabled="false" />
            <InputText v-model="lot.units" inputmode="decimal" placeholder="qty" />
            <InputText
              v-model="lot.price"
              inputmode="decimal"
              placeholder="cost/unit"
              :disabled="openingLotForm.costBasisUnknown"
            />
            <Button
              icon="pi pi-times"
              text
              rounded
              :disabled="openingLotForm.lots.length === 1"
              @click="removeOpeningLot(i)"
            />
          </div>
          <div class="lot-foot">
            <Button label="Add lot" icon="pi pi-plus" text size="small" @click="addOpeningLot" />
            <span class="lot-total">Total: {{ openingLotUnitsTotal }} units</span>
          </div>
          <label>
            Demerger date (optional)
            <InputText v-model="openingLotForm.demergerDate" type="date" />
            <small class="field-hint">
              The demerger's ex-date. Links these lots to the parent and lowers the parent's cost
              basis by the cost recorded here. Without it the lots are saved but the parent isn't
              adjusted.
            </small>
          </label>
        </template>
        <label class="check-row">
          <Checkbox v-model="openingLotForm.costBasisUnknown" :binary="true" />
          Cost basis unknown
        </label>
      </div>
      <template #footer>
        <Button label="Cancel" text @click="openingLotVisible = false" />
        <Button
          label="Save"
          :loading="integrity.recordingOpeningLot"
          :disabled="!openingLotValid"
          @click="submitOpeningLot"
        />
      </template>
    </Dialog>

    <Dialog
      v-model:visible="identityRemapVisible"
      header="Remap to new ISIN"
      modal
      :style="{ width: '24rem' }"
      @hide="identityRemapRow = null"
    >
      <p class="dialog-copy">
        Re-point ledger rows to the current ISIN. Units and amounts stay unchanged.
      </p>
      <label>
        New ISIN
        <InputText v-model="identityRemapIsin" placeholder="INE…" />
      </label>
      <template #footer>
        <Button label="Cancel" text @click="identityRemapVisible = false" />
        <Button
          label="Remap"
          :loading="integrity.applyingIdentityRemap"
          :disabled="!identityRemapIsin.trim()"
          @click="submitIdentityRemap"
        />
      </template>
    </Dialog>

    <CorporateActionForm
      v-model:visible="manualCaVisible"
      :row="manualCaRow"
      :initial-kind="manualCaInitialKind"
      :variant="manualCaVariant"
      :securities="integrity.securitiesFor(investorId)"
      :loading="integrity.applyingManualCorporateAction"
      @submit="submitManualCa"
    />

    <Menu ref="resolveMenu" :model="resolveMenuModel" popup />
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
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  justify-content: flex-end;
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

.ca-preview {
  margin-top: 0.55rem;
  max-width: 32rem;
}
.ca-preview-head {
  margin: 0 0 0.35rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--fm-text-muted);
}
.ca-preview-note {
  margin: 0.4rem 0 0;
  font-size: 0.75rem;
  line-height: 1.45;
  color: var(--fm-text-subtle);
}
.ca-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
  background: var(--fm-surface-2, var(--fm-surface));
  border: 1px solid var(--fm-border);
  border-radius: var(--fm-radius-2, 6px);
  overflow: hidden;
}
.ca-table th,
.ca-table td {
  padding: 0.3rem 0.6rem;
  text-align: left;
  border-bottom: 1px solid var(--fm-border);
}
.ca-table thead th {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  color: var(--fm-text-subtle);
}
.ca-table tbody tr:last-child td {
  border-bottom: 0;
}
.ca-table .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
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

.dialog-copy {
  margin: 0 0 1rem;
  font-size: 0.875rem;
  color: var(--fm-text-muted);
  line-height: 1.45;
}

.dialog-form {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.dialog-form label {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}

.check-row {
  flex-direction: row !important;
  align-items: center;
  gap: 0.5rem !important;
}
.lot-row {
  display: grid;
  grid-template-columns: 1fr 0.7fr 0.9fr auto;
  gap: 0.4rem;
  align-items: center;
}
.lot-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.lot-total {
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
</style>
