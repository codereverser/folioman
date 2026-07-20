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
import { isDesktopShell, pickTradebookFiles } from '@/utils/desktop'
import {
  CANONICAL_COLUMNS,
  CANONICAL_FIELDS,
  autoDetectMapping,
  isValidDematNumber,
  mapCanonicalRows,
  mappingErrors,
  stampImportConstants,
  type Mapping,
} from '@/utils/tradebook'
import { parseTabularFile } from '@/utils/parseTabular'
import IntegrityBadge from '@/components/IntegrityBadge.vue'
import type { IntegrityStatus } from '@/integrity/status'

const router = useRouter()
const ui = useUiStore()
const roster = useRosterStore()
const integrity = useIntegrityStore()
const { readOnly } = useWriteLock()

onMounted(() => void roster.ensureLoaded())

type Step = 'pick' | 'account' | 'map' | 'result'
const step = ref<Step>('pick')
const busy = ref(false)
const errorMessage = ref('')

// --- step 1: investor + files -----------------------------------------------
const investorId = ref<number | null>(ui.selectedInvestorId)
const investorOptions = computed(() =>
  roster.investors.map((i) => ({ label: i.name, value: i.id })),
)
const dragging = ref(false)
const parsing = ref(false)

// A broker exports one tradebook per financial year, so several files are the norm.
// Accumulate them (each pick/drop adds; a file can be removed) and merge their rows
// for a single import — they must share the same columns (same export format).
interface LoadedFile {
  name: string
  rows: Record<string, string>[]
}
const loadedFiles = ref<LoadedFile[]>([])
const headers = ref<string[]>([])
const fileRows = computed(() => loadedFiles.value.flatMap((f) => f.rows))

// A tradebook is small (a few thousand rows); anything past this is almost
// certainly the wrong file, and parsing it on the main thread would freeze the UI.
const MAX_FILE_BYTES = 15 * 1024 * 1024
const ALLOWED_FILE_EXT = /\.(csv|xlsx|xls)$/i
const headerKey = (h: string[]): string => h.map((x) => x.trim().toLowerCase()).join('|')

async function addFiles(picked: File[]): Promise<void> {
  errorMessage.value = ''
  parsing.value = true
  try {
    for (const f of picked) {
      if (!ALLOWED_FILE_EXT.test(f.name)) {
        errorMessage.value = `${f.name}: not a CSV or XLSX export.`
        continue
      }
      if (f.size > MAX_FILE_BYTES) {
        errorMessage.value = `${f.name}: too large (max ${MAX_FILE_BYTES / (1024 * 1024)} MB).`
        continue
      }
      if (loadedFiles.value.some((x) => x.name === f.name)) continue // already added
      let parsed
      try {
        parsed = await parseTabularFile(f)
      } catch {
        errorMessage.value = `${f.name}: couldn’t read it — CSV or XLSX only.`
        continue
      }
      if (loadedFiles.value.length === 0) {
        headers.value = parsed.headers
      } else if (headerKey(parsed.headers) !== headerKey(headers.value)) {
        errorMessage.value = `${f.name}: its columns don’t match the other files — add tradebooks from the same broker export.`
        continue
      }
      loadedFiles.value.push({ name: f.name, rows: parsed.rows })
    }
  } finally {
    parsing.value = false
  }
}

function removeFile(index: number): void {
  loadedFiles.value.splice(index, 1)
  if (loadedFiles.value.length === 0) {
    headers.value = []
    errorMessage.value = ''
  }
}

function onPickFile(event: Event): void {
  const input = event.target as HTMLInputElement
  if (input.files?.length) void addFiles([...input.files])
  input.value = '' // let the same file be re-picked after a remove
}
// In the desktop shell the in-page picker is flaky, so open the native dialog via
// the PyWebView bridge; in a browser this is inert and the <input> handles it.
async function onBrowse(event: Event): Promise<void> {
  if (!isDesktopShell()) return
  event.preventDefault()
  await addFiles(await pickTradebookFiles())
}
function onDrop(event: DragEvent): void {
  dragging.value = false
  const dropped = event.dataTransfer?.files
  if (dropped?.length) void addFiles([...dropped])
}

const canParse = computed(
  () => investorId.value != null && loadedFiles.value.length > 0 && !parsing.value,
)

// Files chosen → ask which demat account this tradebook is for (its own step, so
// the choice isn't missed), then column mapping.
function toAccount(): void {
  mapping.value = autoDetectMapping(headers.value)
  void loadFolios()
  step.value = 'account'
}
function toMap(): void {
  if (accountErrors.value.length === 0) step.value = 'map'
}

// --- steps 2 & 3: demat account + column mapping ----------------------------
const mapping = ref<Mapping>({})
// "— skip —" plus each file header, for the per-field dropdown.
const headerOptions = computed(() => [
  { label: '— skip —', value: '' },
  ...headers.value.map((h) => ({ label: h, value: h })),
])

const demat = ref<Schemas['FolioOut'][]>([])
const folioMode = ref<'existing' | 'manual'>('manual')
const selectedFolioId = ref<number | null>(null)
// Demat identity is captured as DP ID + Client ID (the two values every statement
// shows), not the opaque combined id — terminology varies ("BO ID" means the
// combined on CDSL but the client part on a Zerodha console). We join them into the
// canonical id the eCAS folio also uses, so they match.
const typedDpId = ref('')
const typedClientId = ref('')
const typedBroker = ref('')
const manualDemat = computed(() =>
  (typedDpId.value.trim() + typedClientId.value.trim()).toUpperCase(),
)

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
  demat.value.map((f) => ({
    label: `${f.number}${f.broker ? ` · ${f.broker}` : ''}`,
    value: f.id,
  })),
)

const account = computed<{ folioNumber: string; broker: string }>(() => {
  if (folioMode.value === 'existing') {
    const f = demat.value.find((d) => d.id === selectedFolioId.value)
    return { folioNumber: f?.number ?? '', broker: f?.broker ?? '' }
  }
  return { folioNumber: manualDemat.value, broker: typedBroker.value.trim() }
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
  if (!account.value.folioNumber)
    errs.push('Choose or enter the demat account this tradebook is for.')
  if (folioMode.value === 'manual' && account.value.folioNumber && !account.value.broker) {
    errs.push('Enter the broker name for this account.')
  }
  return errs
})

const blockingErrors = computed(() => [...mappingErrors(mapping.value), ...accountErrors.value])

// Heavy pass — maps every file row; depends only on the file + column mapping, so
// editing the demat account/broker field doesn't rebuild the whole matrix (the
// per-import constants are stamped on at emit time instead).
const mappedRows = computed(() =>
  mappingErrors(mapping.value).length > 0 ? [] : mapCanonicalRows(fileRows.value, mapping.value),
)
// Cheap: restamp only the 20-row preview slice with the demat/broker constants.
const previewRows = computed(() =>
  blockingErrors.value.length > 0
    ? []
    : stampImportConstants(mappedRows.value.slice(0, 20), account.value),
)
// Total importable rows, gated like the preview (hidden until blocking errors clear).
const rowCount = computed(() => (blockingErrors.value.length > 0 ? 0 : mappedRows.value.length))

// --- step 3: import + result ------------------------------------------------
const job = ref<ImportJobOut | null>(null)

interface IncompleteEntry {
  security: string
  isin?: string
  reason: string
  missing_prior_units?: string
  net_units?: string
}
interface CsvResult {
  rows?: number
  created?: number
  skipped?: number
  errors?: { row: number; error: string }[]
  incomplete_history?: IncompleteEntry[]
  unresolved_securities?: { name: string; isin: string; symbol: string }[]
  reconcile_errors?: unknown
}
const result = computed<CsvResult>(() => (job.value?.result as CsvResult) ?? {})
const succeeded = computed(
  () => job.value?.status === 'success' || job.value?.status === 'completed_with_warnings',
)

// Holdings-anchor reconciliation for the demat account this import targeted: each
// (security, folio) ledger-net vs the eCAS closing balance. Fetched after import
// (it's written by the server's post-import reconcile), scoped to this folio.
const integrityRows = ref<Schemas['IntegrityStatusOut'][]>([])
async function loadReconciliation(): Promise<void> {
  if (investorId.value == null) return
  const res = await api.GET('/api/investors/{investor_id}/integrity', {
    params: { path: { investor_id: investorId.value } },
  })
  // Match the folio this import targeted. For an existing demat we hold its id
  // (exact). For a manually-entered number we compare case/space-folded strings —
  // the server stores the number we sent, and we don't learn the new folio's id
  // back from the import result, so a raw `===` could miss on trivial formatting.
  const norm = (s: string | null | undefined) => (s ?? '').replace(/\s+/g, '').toUpperCase()
  const targetId = folioMode.value === 'existing' ? selectedFolioId.value : null
  const targetNumber = norm(account.value.folioNumber)
  integrityRows.value = (res.data ?? []).filter((r) =>
    targetId != null ? r.folio?.id === targetId : norm(r.folio?.number) === targetNumber,
  )
}
// Full-history securities: a clean ledger (no opposing snapshot → full_history) or
// one that matches its eCAS holding (reconciled). Counted across all rows, so a
// tradebook with no eCAS yet still reports its full-history securities.
const reconciledCount = computed(
  () =>
    integrityRows.value.filter((r) => r.status === 'reconciled' || r.status === 'full_history')
      .length,
)
const mismatchRows = computed(() => integrityRows.value.filter((r) => r.status === 'mismatch'))
// Securities with an eCAS holding to reconcile against (others have no anchor yet).
const anchorRows = computed(() => integrityRows.value.filter((r) => r.units_from_holdings != null))

async function runImport(): Promise<void> {
  if (investorId.value == null || blockingErrors.value.length > 0 || busy.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    const csv = toCsv(CANONICAL_COLUMNS, stampImportConstants(mappedRows.value, account.value))
    const label =
      loadedFiles.value.length === 1
        ? loadedFiles.value[0].name
        : `${loadedFiles.value.length} tradebooks`
    job.value = await importTransactionsCsv(investorId.value, csv, label)
    step.value = 'result'
    if (succeeded.value) {
      ui.notify({ severity: 'success', summary: 'Import complete', detail: label })
      integrity.clear()
      await loadReconciliation()
    }
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Import failed'
  } finally {
    busy.value = false
  }
}

function reset(): void {
  step.value = 'pick'
  loadedFiles.value = []
  headers.value = []
  mapping.value = {}
  job.value = null
  integrityRows.value = []
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
      <RouterLink class="back" :to="{ name: 'import' }">
        <i class="pi pi-arrow-left" aria-hidden="true" /> Import
      </RouterLink>
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
        <input
          type="file"
          accept=".csv,.xlsx,.xls,text/csv"
          class="file-input"
          multiple
          aria-label="Choose one or more tradebook CSV or XLSX files"
          @change="onPickFile"
        />
        <i class="pi pi-file-excel" aria-hidden="true" />
        <span v-if="parsing" class="dropzone-hint"
          ><i class="pi pi-spin pi-spinner" aria-hidden="true" /> Reading…</span
        >
        <span v-else class="dropzone-hint">
          Drop CSV/XLSX tradebooks here, or click to add —
          <strong>you can select several</strong> (brokers often limit each export's date range, so
          a full history comes as multiple files).
        </span>
      </label>

      <!-- Added files: each removable; user keeps adding until they map. -->
      <ul v-if="loadedFiles.length" class="file-list">
        <li v-for="(f, i) in loadedFiles" :key="f.name">
          <i class="pi pi-file" aria-hidden="true" />
          <span class="f-name">{{ f.name }}</span>
          <span class="muted small">{{ f.rows.length }} rows</span>
          <button
            type="button"
            class="f-remove"
            :aria-label="`Remove ${f.name}`"
            @click.stop="removeFile(i)"
          >
            <i class="pi pi-times" aria-hidden="true" />
          </button>
        </li>
      </ul>
      <p v-if="loadedFiles.length && !parsing" class="muted small">
        {{ loadedFiles.length }} file{{ loadedFiles.length > 1 ? 's' : '' }} ·
        {{ headers.length }} columns · {{ fileRows.length }} rows. Add more, or map the columns.
      </p>

      <Message v-if="errorMessage" severity="error" :closable="false">{{ errorMessage }}</Message>

      <div class="actions">
        <Button
          label="Continue"
          icon="pi pi-arrow-right"
          icon-pos="right"
          :disabled="!canParse || readOnly"
          @click="toAccount"
        />
      </div>
    </div>

    <!-- Step 2: demat account — decided on its own so it can't be missed; it drives
         the reconcile against eCAS holdings. -->
    <div v-else-if="step === 'account'" class="card">
      <h2 class="step-title">Which demat account?</h2>
      <p class="muted small">
        Which demat account is this tradebook for? Matching the real account lets it reconcile
        against your eCAS holdings.
      </p>

      <!-- Nudge: importing the eCAS first gives the demat to pick (no typing) and is
           what anchors net worth + reconciliation. Shown when none is on file yet. -->
      <Message v-if="!demat.length" severity="info" :closable="false">
        Tip: import your latest <strong>eCAS</strong> (NSDL/CDSL) first — then pick the demat here
        instead of typing it, and we can reliably track net worth and reconcile your holdings.
      </Message>

      <fieldset v-if="demat.length" class="field demat-modes">
        <legend class="field-label">How is this account identified?</legend>
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
          aria-label="Choose a demat account"
        />
        <label class="radio">
          <input v-model="folioMode" type="radio" value="manual" /> Enter the DP ID + Client ID
        </label>
      </fieldset>
      <template v-if="folioMode === 'manual'">
        <div class="demat-id-row">
          <label class="field">
            <span class="field-label">DP ID</span>
            <input
              v-model="typedDpId"
              type="text"
              class="text-input"
              inputmode="text"
              placeholder="e.g. 12081600 (or IN303…)"
            />
          </label>
          <label class="field">
            <span class="field-label">Client ID</span>
            <input
              v-model="typedClientId"
              type="text"
              class="text-input"
              inputmode="numeric"
              placeholder="e.g. 14771491"
            />
          </label>
        </div>
        <p class="muted small">
          On a Zerodha/CDSL console the Client ID is shown as “BO ID”; on NSDL it’s the Client ID.
          <template v-if="manualDemat"
            >Demat ID: <code>{{ manualDemat }}</code></template
          >
        </p>
        <label class="field">
          <span class="field-label">Broker</span>
          <input v-model="typedBroker" type="text" class="text-input" placeholder="e.g. Zerodha" />
        </label>
        <Message v-if="dematWarning" severity="warn" :closable="false">{{ dematWarning }}</Message>
      </template>

      <Message v-if="accountErrors.length" severity="warn" :closable="false">
        <ul class="err-list">
          <li v-for="e in accountErrors" :key="e">{{ e }}</li>
        </ul>
      </Message>

      <div class="actions">
        <Button label="Back" severity="secondary" outlined @click="step = 'pick'" />
        <Button
          label="Map columns"
          icon="pi pi-arrow-right"
          icon-pos="right"
          :disabled="accountErrors.length > 0 || readOnly"
          @click="toMap"
        />
      </div>
    </div>

    <!-- Step 3: map columns -->
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
            :aria-label="`Source column for ${f.label}`"
          />
        </div>
      </div>

      <Message v-if="blockingErrors.length" severity="warn" :closable="false">
        <ul class="err-list">
          <li v-for="e in blockingErrors" :key="e">{{ e }}</li>
        </ul>
      </Message>

      <!-- Preview: the target account + a sample of the mapped rows. -->
      <template v-if="rowCount">
        <h2 class="step-title">Preview</h2>
        <p v-if="account.folioNumber" class="muted small">
          Importing <strong>{{ rowCount }}</strong> rows into demat
          <code>{{ account.folioNumber }}</code
          ><template v-if="account.broker"> · {{ account.broker }}</template
          >.
        </p>
        <DataTable :value="previewRows" size="small" class="preview">
          <Column field="date" header="Date" />
          <Column field="transaction_type" header="Type" />
          <Column field="name" header="Security" />
          <Column field="units" header="Units" class="num" header-class="num" />
          <Column field="price" header="Price" class="num" header-class="num" />
        </DataTable>
        <p v-if="rowCount > previewRows.length" class="muted small">
          Showing first {{ previewRows.length }} of {{ rowCount }}.
        </p>
      </template>

      <Message v-if="errorMessage" severity="error" :closable="false">{{ errorMessage }}</Message>

      <div class="actions">
        <Button
          label="Back"
          severity="secondary"
          outlined
          :disabled="busy"
          @click="step = 'account'"
        />
        <Button
          label="Import"
          icon="pi pi-upload"
          :loading="busy"
          :disabled="blockingErrors.length > 0 || readOnly"
          @click="runImport"
        />
      </div>
    </div>

    <!-- Step 4: result -->
    <div v-else class="card">
      <Message v-if="job?.status === 'failed'" severity="error" :closable="false">
        {{ job?.error }}
      </Message>
      <template v-if="succeeded">
        <Message severity="success" :closable="false">
          Imported {{ result.created ?? 0 }} transaction(s)<span v-if="result.skipped"
            >, {{ result.skipped }} already on file</span
          >.
        </Message>

        <!-- Completeness scorecard: at a glance, what's tax-ready vs not. -->
        <div class="scorecard">
          <div class="score">
            <span class="score-num">{{ reconciledCount }}</span>
            <span class="score-label">full history</span>
          </div>
          <div class="score" :class="{ flag: (result.incomplete_history?.length ?? 0) > 0 }">
            <span class="score-num">{{ result.incomplete_history?.length ?? 0 }}</span>
            <span class="score-label">incomplete</span>
          </div>
          <div class="score" :class="{ flag: mismatchRows.length > 0 }">
            <span class="score-num">{{ mismatchRows.length }}</span>
            <span class="score-label">eCAS mismatch</span>
          </div>
          <div class="score" :class="{ flag: (result.errors?.length ?? 0) > 0 }">
            <span class="score-num">{{ result.errors?.length ?? 0 }}</span>
            <span class="score-label">skipped rows</span>
          </div>
        </div>

        <!-- Holdings-anchor reconciliation: ledger net vs the eCAS closing balance. -->
        <div v-if="anchorRows.length" class="panel">
          <h2 class="step-title">Reconciled against your eCAS holdings</h2>
          <ul class="recon">
            <li v-for="r in anchorRows" :key="`${r.security.id}-${r.folio?.id ?? r.folio?.number}`">
              <span class="sec-name">{{ r.security.name }}</span>
              <span class="recon-units muted small">
                ledger {{ r.units_from_transactions ?? 0 }} · eCAS {{ r.units_from_holdings }}
              </span>
              <IntegrityBadge :status="r.status as IntegrityStatus" size="sm" />
            </li>
          </ul>
          <p v-if="mismatchRows.length" class="muted small">
            A mismatch means the tradebook’s net units differ from your demat holding — a known gap
            you can acknowledge on the Integrity page (it won’t corrupt the ledger).
          </p>
        </div>

        <!-- Incomplete cost basis: orphan sells, excluded from P&L / tax. -->
        <div v-if="result.incomplete_history?.length" class="warn-block">
          <h2>Incomplete cost basis ({{ result.incomplete_history.length }})</h2>
          <p class="muted small">
            These securities have sells with no matching buy — earlier trades are missing, so
            <strong>no realized gains or tax</strong> are computed for them. Import an
            earlier-period tradebook to complete them.
          </p>
          <ul class="incomplete">
            <li v-for="(s, i) in result.incomplete_history" :key="s.isin || `${s.security}-${i}`">
              <span class="sec-name">{{ s.security }}</span>
              <span class="muted small"
                >{{ s.missing_prior_units }} units missing before the file</span
              >
            </li>
          </ul>
        </div>

        <div v-if="result.unresolved_securities?.length" class="warn-block">
          <h2>Couldn’t identify ({{ result.unresolved_securities.length }})</h2>
          <p class="muted small">
            We couldn’t match these to a listed scrip (a delisted/SME/foreign symbol, or an ISIN our
            database doesn’t know yet), so they stay unpriced under a provisional name until a later
            update resolves them.
          </p>
          <ul class="incomplete">
            <li v-for="s in result.unresolved_securities" :key="s.isin || s.symbol">
              <span class="sec-name">{{ s.symbol || s.name }}</span>
              <span class="muted small">{{ s.isin }}</span>
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
        <Button
          label="Import another"
          severity="secondary"
          outlined
          icon="pi pi-replay"
          @click="reset"
        />
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
.back {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  margin-bottom: var(--fm-space-2);
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--fm-text-muted);
  text-decoration: none;
}
.back:hover {
  color: var(--p-primary-color);
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
/* The demat-mode radios are grouped in a <fieldset> for screen-reader semantics;
   strip the browser's default fieldset chrome so it looks like a plain .field. */
.demat-modes {
  margin: 0;
  padding: 0;
  border: 0;
  min-width: 0;
}
/* DP ID + Client ID side by side; stack on a narrow viewport. */
.demat-id-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fm-space-3);
}
.demat-id-row .field {
  flex: 1 1 10rem;
  min-width: 0;
}
.demat-modes > legend {
  padding: 0;
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
  transition:
    border-color var(--fm-dur) var(--fm-ease),
    background var(--fm-dur) var(--fm-ease);
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
.file-list {
  list-style: none;
  margin: var(--fm-space-3) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-2);
}
.file-list li {
  display: flex;
  align-items: center;
  gap: var(--fm-space-2);
  padding: 0.4rem 0.6rem;
  background: var(--fm-surface-raised);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-sm);
}
.file-list .f-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}
.f-remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.6rem;
  height: 1.6rem;
  border: none;
  background: transparent;
  color: var(--fm-text-subtle);
  border-radius: var(--fm-radius-sm);
  cursor: pointer;
}
.f-remove:hover {
  background: var(--fm-surface);
  color: var(--fm-loss);
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
/* Completeness scorecard */
.scorecard {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--fm-space-2);
}
.score {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  padding: var(--fm-space-3);
  background: var(--fm-surface);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-md);
  text-align: center;
}
.score.flag {
  border-color: color-mix(in oklab, var(--fm-warn) 55%, var(--fm-border));
  background: var(--fm-warn-bg);
}
.score-num {
  font-size: 1.4rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.score-label {
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--fm-text-muted);
}

/* Reconciliation panel (ledger vs eCAS) */
.panel {
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-md);
  padding: var(--fm-space-4);
}
.recon {
  list-style: none;
  margin: var(--fm-space-3) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-2);
}
.recon li {
  display: flex;
  align-items: center;
  gap: var(--fm-space-3);
}
.recon li .sec-name {
  flex: 1;
  min-width: 0;
}
.recon-units {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
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
