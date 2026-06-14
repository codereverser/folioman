<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Message from 'primevue/message'
import Select from 'primevue/select'
import { api, importTransactionsCsv, type ImportJobOut, type Schemas } from '@/api/client'
import { useUiStore } from '@/stores/ui'
import { useRosterStore } from '@/stores/roster'
import { useIntegrityStore } from '@/stores/integrity'
import { useWriteLock } from '@/composables/useWriteLock'
import { toCsv } from '@/utils/csv'
import { isDesktopShell, pickTradebookFile } from '@/utils/desktop'
import {
  CANONICAL_COLUMNS,
  CANONICAL_FIELDS,
  autoDetectMapping,
  buildCanonicalRows,
  isValidDematNumber,
  mappingErrors,
  type Mapping,
} from '@/utils/tradebook'
import { parseTabularFile } from '@/utils/parseTabular'

const router = useRouter()
const ui = useUiStore()
const roster = useRosterStore()
const integrity = useIntegrityStore()
const { readOnly } = useWriteLock()

onMounted(() => void roster.ensureLoaded())

type Step = 'pick' | 'map' | 'result'
const step = ref<Step>('pick')
const busy = ref(false)
const errorMessage = ref('')

// --- step 1: investor + file ------------------------------------------------
const investorId = ref<number | null>(ui.selectedInvestorId)
const investorOptions = computed(() =>
  roster.investors.map((i) => ({ label: i.name, value: i.id })),
)
const file = ref<File | null>(null)
const dragging = ref(false)
const headers = ref<string[]>([])
const fileRows = ref<Record<string, string>[]>([])

function onPickFile(event: Event): void {
  const picked = (event.target as HTMLInputElement).files?.[0] ?? null
  void loadFile(picked)
}
// In the desktop shell the in-page picker is flaky, so open the native dialog via
// the PyWebView bridge; in a browser this is inert and the <input> handles it.
async function onBrowse(event: Event): Promise<void> {
  if (!isDesktopShell()) return
  event.preventDefault()
  const picked = await pickTradebookFile()
  if (picked) void loadFile(picked)
}
function onDrop(event: DragEvent): void {
  dragging.value = false
  const dropped = event.dataTransfer?.files?.[0]
  if (dropped) void loadFile(dropped)
}
async function loadFile(picked: File | null): Promise<void> {
  file.value = picked
  errorMessage.value = ''
  if (!picked) return
  try {
    const parsed = await parseTabularFile(picked)
    headers.value = parsed.headers
    fileRows.value = parsed.rows
  } catch {
    errorMessage.value = 'Could not read this file — upload a CSV or XLSX export.'
    headers.value = []
    fileRows.value = []
  }
}

const canParse = computed(
  () => investorId.value != null && file.value != null && headers.value.length > 0,
)

function toMapping(): void {
  mapping.value = autoDetectMapping(headers.value)
  void loadFolios()
  step.value = 'map'
}

// --- step 2: column mapping + demat account ---------------------------------
const mapping = ref<Mapping>({})
// "— skip —" plus each file header, for the per-field dropdown.
const headerOptions = computed(() => [
  { label: '— skip —', value: '' },
  ...headers.value.map((h) => ({ label: h, value: h })),
])

const demat = ref<Schemas['FolioOut'][]>([])
const folioMode = ref<'existing' | 'manual'>('manual')
const selectedFolioId = ref<number | null>(null)
const typedNumber = ref('')
const typedBroker = ref('')

async function loadFolios(): Promise<void> {
  if (investorId.value == null) return
  const res = await api.GET('/api/investors/{investor_id}/folios', {
    params: { path: { investor_id: investorId.value } },
  })
  demat.value = (res.data ?? []).filter((f) => f.folio_type === 'demat')
  if (demat.value.length > 0) {
    folioMode.value = 'existing'
    selectedFolioId.value = demat.value[0].id
  } else {
    folioMode.value = 'manual'
  }
}
const folioOptions = computed(() =>
  demat.value.map((f) => ({ label: `${f.number}${f.broker ? ` · ${f.broker}` : ''}`, value: f.id })),
)

const account = computed<{ folioNumber: string; broker: string }>(() => {
  if (folioMode.value === 'existing') {
    const f = demat.value.find((d) => d.id === selectedFolioId.value)
    return { folioNumber: f?.number ?? '', broker: f?.broker ?? '' }
  }
  return { folioNumber: typedNumber.value.trim().toUpperCase(), broker: typedBroker.value.trim() }
})

// A typed number gets a soft format warning; an existing folio is always valid.
const dematWarning = computed(() => {
  if (folioMode.value !== 'manual') return ''
  const n = account.value.folioNumber
  if (!n) return ''
  return isValidDematNumber(n)
    ? ''
    : 'This doesn’t look like a demat account number (16-digit CDSL, or NSDL “IN” + 14 digits). Double-check it.'
})

const accountErrors = computed(() => {
  const errs: string[] = []
  if (!account.value.folioNumber) errs.push('Choose or enter the demat account this tradebook is for.')
  if (folioMode.value === 'manual' && account.value.folioNumber && !account.value.broker) {
    errs.push('Enter the broker name for this account.')
  }
  return errs
})

const blockingErrors = computed(() => [...mappingErrors(mapping.value), ...accountErrors.value])

const canonicalRows = computed(() =>
  blockingErrors.value.length > 0
    ? []
    : buildCanonicalRows(fileRows.value, mapping.value, account.value),
)
const previewRows = computed(() => canonicalRows.value.slice(0, 20))

// --- step 3: import + result ------------------------------------------------
const job = ref<ImportJobOut | null>(null)

interface CsvResult {
  rows?: number
  created?: number
  skipped?: number
  errors?: { row: number; error: string }[]
  incomplete_history?: { security: string; reason: string; missing_prior_units?: string }[]
  reconcile_errors?: unknown
}
const result = computed<CsvResult>(() => (job.value?.result as CsvResult) ?? {})
const succeeded = computed(
  () => job.value?.status === 'success' || job.value?.status === 'completed_with_warnings',
)

async function runImport(): Promise<void> {
  if (investorId.value == null || blockingErrors.value.length > 0 || busy.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    const csv = toCsv(CANONICAL_COLUMNS, canonicalRows.value)
    job.value = await importTransactionsCsv(investorId.value, csv, file.value?.name ?? 'tradebook.csv')
    step.value = 'result'
    if (succeeded.value) {
      ui.notify({ severity: 'success', summary: 'Import complete', detail: file.value?.name })
      integrity.clear()
    }
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Import failed'
  } finally {
    busy.value = false
  }
}

function reset(): void {
  step.value = 'pick'
  file.value = null
  headers.value = []
  fileRows.value = []
  mapping.value = {}
  job.value = null
  errorMessage.value = ''
}

function goToDashboard(): void {
  if (investorId.value == null) return
  ui.selectInvestor(investorId.value)
  void router.push({ name: 'dashboard', params: { investorId: investorId.value } })
}

// Re-parse if the investor changes after a file is loaded (account list differs).
watch(investorId, () => {
  if (step.value !== 'pick') reset()
})
</script>

<template>
  <section class="tb-page">
    <header class="page-head">
      <h1>Import a broker tradebook</h1>
      <p class="muted lede">
        Upload a stock tradebook (CSV or XLSX) from any broker, map its columns once, and we’ll
        build a transaction ledger with cost basis and capital gains.
      </p>
    </header>

    <Message v-if="readOnly" severity="info" :closable="false">
      Importing is disabled on this read-only demo.
    </Message>

    <!-- Step 1: investor + file -->
    <div v-if="step === 'pick'" class="card">
      <label class="field">
        <span class="field-label">Investor</span>
        <Select
          v-model="investorId"
          :options="investorOptions"
          option-label="label"
          option-value="value"
          placeholder="Choose an investor"
          filter
        />
      </label>

      <label
        class="dropzone"
        :class="{ dragging }"
        @click="onBrowse"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <input type="file" accept=".csv,.xlsx,.xls,text/csv" class="file-input" @change="onPickFile" />
        <i class="pi pi-file-excel" aria-hidden="true" />
        <span v-if="file" class="file-name">{{ file.name }}</span>
        <span v-else class="dropzone-hint">Drop a CSV/XLSX tradebook here, or click to browse</span>
      </label>
      <p v-if="file && headers.length" class="muted small">
        {{ headers.length }} columns, {{ fileRows.length }} rows detected.
      </p>

      <Message v-if="errorMessage" severity="error" :closable="false">{{ errorMessage }}</Message>

      <div class="actions">
        <Button
          label="Map columns"
          icon="pi pi-arrow-right"
          icon-pos="right"
          :disabled="!canParse || readOnly"
          @click="toMapping"
        />
      </div>
    </div>

    <!-- Step 2: map columns + demat account -->
    <div v-else-if="step === 'map'" class="card">
      <h2 class="step-title">Map your columns</h2>
      <p class="muted small">
        We matched what we could — fix anything that’s off. Required fields are marked
        <span class="req">*</span>.
      </p>
      <div class="map-grid">
        <div v-for="f in CANONICAL_FIELDS" :key="f.key" class="map-row">
          <span class="map-label">{{ f.label }}<span v-if="f.required" class="req">*</span></span>
          <Select
            v-model="mapping[f.key]"
            :options="headerOptions"
            option-label="label"
            option-value="value"
            class="map-select"
          />
        </div>
      </div>

      <h2 class="step-title">Demat account</h2>
      <p class="muted small">
        Which demat account is this tradebook for? Using the real account number lets it reconcile
        against your eCAS holdings.
      </p>
      <div v-if="demat.length" class="field">
        <label class="radio">
          <input v-model="folioMode" type="radio" value="existing" /> Pick an account I’ve imported
        </label>
        <Select
          v-if="folioMode === 'existing'"
          v-model="selectedFolioId"
          :options="folioOptions"
          option-label="label"
          option-value="value"
          placeholder="Choose a demat account"
        />
        <label class="radio">
          <input v-model="folioMode" type="radio" value="manual" /> Enter an account number
        </label>
      </div>
      <template v-if="folioMode === 'manual'">
        <label class="field">
          <span class="field-label">Demat account number (BO ID)</span>
          <input
            v-model="typedNumber"
            type="text"
            class="text-input"
            placeholder="e.g. 1208160000000001 or IN30021412345678"
          />
        </label>
        <label class="field">
          <span class="field-label">Broker</span>
          <input v-model="typedBroker" type="text" class="text-input" placeholder="e.g. Zerodha" />
        </label>
        <Message v-if="dematWarning" severity="warn" :closable="false">{{ dematWarning }}</Message>
      </template>

      <Message v-if="blockingErrors.length" severity="warn" :closable="false">
        <ul class="err-list">
          <li v-for="e in blockingErrors" :key="e">{{ e }}</li>
        </ul>
      </Message>

      <!-- Live preview of the mapped canonical rows -->
      <template v-if="canonicalRows.length">
        <h2 class="step-title">Preview ({{ canonicalRows.length }} rows)</h2>
        <DataTable :value="previewRows" size="small" class="preview">
          <Column field="date" header="Date" />
          <Column field="transaction_type" header="Type" />
          <Column field="name" header="Security" />
          <Column field="units" header="Units" />
          <Column field="price" header="Price" />
        </DataTable>
        <p v-if="canonicalRows.length > previewRows.length" class="muted small">
          Showing first {{ previewRows.length }} of {{ canonicalRows.length }}.
        </p>
      </template>

      <Message v-if="errorMessage" severity="error" :closable="false">{{ errorMessage }}</Message>

      <div class="actions">
        <Button label="Back" severity="secondary" outlined :disabled="busy" @click="reset" />
        <Button
          label="Import"
          icon="pi pi-upload"
          :loading="busy"
          :disabled="blockingErrors.length > 0 || readOnly"
          @click="runImport"
        />
      </div>
    </div>

    <!-- Step 3: result -->
    <div v-else class="card">
      <Message v-if="job?.status === 'failed'" severity="error" :closable="false">
        {{ job?.error }}
      </Message>
      <template v-if="succeeded">
        <Message severity="success" :closable="false">
          Imported {{ result.created ?? 0 }} transaction(s)<span v-if="result.skipped">,
          {{ result.skipped }} already on file</span>.
        </Message>

        <div v-if="result.incomplete_history?.length" class="warn-block">
          <h2>Incomplete cost basis ({{ result.incomplete_history.length }})</h2>
          <p class="muted small">
            These securities have sells with no matching buy — earlier trades are missing, so we
            can’t compute realized gains or tax for them yet. Import an earlier-period tradebook to
            complete them.
          </p>
          <ul class="incomplete">
            <li v-for="s in result.incomplete_history" :key="s.security">
              <span class="sec-name">{{ s.security }}</span>
              <span class="muted small">{{ s.missing_prior_units }} units missing before the file</span>
            </li>
          </ul>
        </div>

        <div v-if="result.errors?.length" class="warn-block">
          <h2>Skipped rows ({{ result.errors.length }})</h2>
          <ul class="incomplete">
            <li v-for="e in result.errors.slice(0, 20)" :key="e.row">
              <span class="muted">Row {{ e.row }}:</span> <span class="reason">{{ e.error }}</span>
            </li>
          </ul>
        </div>

        <Message v-if="result.reconcile_errors" severity="warn" :closable="false">
          Imported, but some securities couldn’t be reconciled — they may show as needing attention.
        </Message>
      </template>

      <div class="actions">
        <Button label="Import another" severity="secondary" outlined icon="pi pi-replay" @click="reset" />
        <Button
          v-if="succeeded"
          label="View dashboard"
          icon="pi pi-arrow-right"
          icon-pos="right"
          @click="goToDashboard"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
.tb-page {
  max-width: 48rem;
  margin: 0 auto;
  padding: var(--fm-space-6);
}
.page-head {
  margin-bottom: var(--fm-space-5);
}
.page-head h1 {
  margin: 0 0 var(--fm-space-2);
  font-size: 1.5rem;
  font-weight: 600;
}
.muted {
  color: var(--fm-text-muted);
}
.small {
  font-size: 0.8125rem;
}
.lede {
  margin: 0;
  max-width: 40rem;
}
.card {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-4);
  background: var(--fm-surface-raised);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-lg);
  padding: var(--fm-space-5);
}
.step-title {
  margin: var(--fm-space-2) 0 0;
  font-size: 1rem;
  font-weight: 600;
}
.field {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-2);
}
.field-label {
  font-size: 0.875rem;
  font-weight: 500;
}
.text-input {
  padding: 0.5rem 0.75rem;
  font: inherit;
  color: var(--fm-text);
  background: var(--fm-surface);
  border: 1px solid var(--fm-border);
  border-radius: var(--fm-radius-sm);
}
.text-input:focus {
  outline: 2px solid var(--fm-verified);
  outline-offset: -1px;
}
.radio {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
}
.dropzone {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--fm-space-2);
  min-height: 8rem;
  padding: var(--fm-space-6);
  text-align: center;
  border: 1.5px dashed var(--fm-border);
  border-radius: var(--fm-radius-md);
  cursor: pointer;
  transition: border-color var(--fm-dur) var(--fm-ease), background var(--fm-dur) var(--fm-ease);
}
.dropzone:hover,
.dropzone.dragging {
  border-color: var(--fm-verified);
  background: var(--fm-verified-bg);
}
.dropzone .pi {
  font-size: 1.75rem;
  color: var(--fm-text-subtle);
}
.file-input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}
.file-name {
  font-weight: 600;
}
.dropzone-hint {
  color: var(--fm-text-muted);
}
.map-grid {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-2);
}
.map-row {
  display: grid;
  grid-template-columns: 1fr 1.4fr;
  align-items: center;
  gap: var(--fm-space-3);
}
.map-label {
  font-size: 0.875rem;
}
.map-select {
  width: 100%;
}
.req {
  color: var(--p-red-500, #ef4444);
  margin-left: 0.15rem;
}
.err-list {
  margin: 0;
  padding-left: 1.1rem;
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--fm-space-2);
}
.warn-block {
  border: 1px solid var(--fm-warn);
  background: var(--fm-warn-bg);
  border-radius: var(--fm-radius-md);
  padding: var(--fm-space-4);
}
.warn-block h2 {
  margin: 0 0 var(--fm-space-1);
  font-size: 1rem;
  font-weight: 600;
}
.incomplete {
  list-style: none;
  margin: var(--fm-space-3) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-2);
}
.incomplete li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--fm-space-2);
}
.sec-name {
  font-weight: 500;
}
.reason {
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
</style>
