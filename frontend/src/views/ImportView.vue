<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import Message from 'primevue/message'
import { importCas, previewCas, type CasPreviewOut, type ImportJobOut } from '@/api/client'
import { useUiStore } from '@/stores/ui'

const router = useRouter()
const ui = useUiStore()

// --- result view-model ------------------------------------------------------
// The job's `result` is an open dict on the wire; narrow it to the keys the two
// import paths actually set (see the server's `process_cas`).
interface IncompleteScheme {
  security: string
  folio: string
  reason: string
  opening_units: string | null
  closing_units: string | null
}
interface Removal {
  name: string
  isin: string
  units: string
}
interface CasResult {
  detected?: 'mf_cas' | 'ecas'
  notice?: string
  // MF CAS
  schemes?: number
  transactions_created?: number
  transactions_skipped?: number
  holdings_snapshotted?: number
  securities?: number
  incomplete_history?: IncompleteScheme[]
  // eCAS
  accounts?: number
  holdings_created?: number
  holdings_updated?: number
  holdings_removed?: number
  removed?: Removal[]
  // destructive-eCAS confirmation gate
  requires_confirmation?: boolean
  statement_date?: string
  removals?: Removal[]
  reconcile_errors?: unknown
}

const REASON_LABELS: Record<string, string> = {
  opening_nonzero: 'Opening balance isn’t zero — earlier transactions are missing.',
  history_gap: 'Doesn’t continue from the statement already on file.',
  rows_unreconciled: 'Some rows couldn’t be matched to the closing balance.',
}
function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? reason
}

// --- upload state -----------------------------------------------------------
const file = ref<File | null>(null)
const password = ref('')
const dragging = ref(false)
const busy = ref(false)
// preview (who the statement belongs to) -> import (job). The statement carries
// the owner's PAN, so the server resolves/creates the investor — none is chosen
// up front. We show the identity for confirmation before writing anything.
const preview = ref<CasPreviewOut | null>(null)
const job = ref<ImportJobOut | null>(null)
const errorMessage = ref('')

const result = computed<CasResult>(() => (job.value?.result as CasResult) ?? {})
const status = computed(() => job.value?.status ?? '')
const needsConfirmation = computed(() => status.value === 'needs_confirmation')
const failed = computed(() => status.value === 'failed')
const succeeded = computed(
  () => status.value === 'success' || status.value === 'completed_with_warnings',
)
const kindLabel = computed(() =>
  preview.value?.kind === 'ecas' ? 'Demat eCAS (NSDL/CDSL)' : 'Mutual-fund CAS (CAMS/KFin)',
)

function clearFrom(file_: File | null): void {
  file.value = file_
  preview.value = null
  job.value = null
  errorMessage.value = ''
}
function pickFile(event: Event): void {
  clearFrom((event.target as HTMLInputElement).files?.[0] ?? null)
}
function onDrop(event: DragEvent): void {
  dragging.value = false
  const dropped = event.dataTransfer?.files?.[0]
  if (dropped) clearFrom(dropped)
}

// Step 1: parse the file and report whose it is — nothing is persisted yet.
async function review(): Promise<void> {
  if (!file.value || busy.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    preview.value = await previewCas(file.value, password.value)
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Could not read this statement'
  } finally {
    busy.value = false
  }
}

// Step 2: resolve/create the investor and import. `confirm` re-runs a destructive
// eCAS (one that would remove holdings) after the user accepts the removals.
async function submit(confirm = false): Promise<void> {
  if (!file.value || busy.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    job.value = await importCas(file.value, password.value, confirm)
    if (succeeded.value) {
      ui.notify({ severity: 'success', summary: 'Import complete', detail: file.value.name })
    }
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Import failed'
  } finally {
    busy.value = false
  }
}

function reset(): void {
  clearFrom(null)
  password.value = ''
}

function goToDashboard(): void {
  const id = job.value?.investor_id
  if (id == null) return
  ui.selectInvestor(id)
  void router.push({ name: 'dashboard', params: { investorId: id } })
}
</script>

<template>
  <section class="import-page">
    <header class="page-head">
      <h1>Import CAS</h1>
      <p class="muted">
        Upload a CAMS/KFin <strong>MF CAS</strong> or an NSDL/CDSL <strong>eCAS</strong> — the
        type is auto-detected. An MF CAS builds a full transaction ledger; an eCAS refreshes
        demat holdings as a net-worth snapshot only.
      </p>
      <p class="scope-note">
        Right now that's the whole story — mutual funds from a CAS and demat holdings from an
        eCAS. Stocks, bonds and the rest still show up from your eCAS as snapshots; typing in
        your own transactions is coming later.
      </p>
    </header>

    <!-- Step 1: pick the file (nothing is persisted until you confirm who it's for) -->
    <div v-if="!preview && !job" class="card">
      <label
        class="dropzone"
        :class="{ dragging }"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <input type="file" accept="application/pdf,.pdf" class="file-input" @change="pickFile" />
        <i class="pi pi-file-pdf" aria-hidden="true" />
        <span v-if="file" class="file-name">{{ file.name }}</span>
        <span v-else class="dropzone-hint">Drop a CAS PDF here, or click to browse</span>
      </label>

      <label class="field">
        <span class="field-label">PDF password <span class="muted">(if protected)</span></span>
        <input
          v-model="password"
          type="password"
          class="text-input"
          autocomplete="off"
          placeholder="Leave blank if none"
        />
      </label>

      <Message v-if="errorMessage" severity="error" :closable="false">{{ errorMessage }}</Message>

      <div class="actions">
        <Button label="Continue" icon="pi pi-arrow-right" icon-pos="right" :disabled="!file || busy" :loading="busy" @click="review" />
      </div>
    </div>

    <!-- Step 2: confirm who the statement belongs to before importing -->
    <div v-else-if="!job" class="card">
      <Message severity="info" :closable="false">
        We read the holder's PAN from this {{ kindLabel }}.
        <template v-if="preview?.match_investor_id">
          It belongs to an investor you already track.
        </template>
        <template v-else>It'll create a new investor.</template>
      </Message>
      <dl class="summary">
        <div><dt>Statement</dt><dd>{{ kindLabel }}</dd></div>
        <div>
          <dt>{{ preview?.match_investor_id ? 'Existing investor' : 'New investor' }}</dt>
          <dd>{{ preview?.match_investor_name || preview?.investor_name || '—' }}</dd>
        </div>
        <div><dt>PAN</dt><dd class="mono">{{ preview?.pan_masked }}</dd></div>
      </dl>

      <Message v-if="errorMessage" severity="error" :closable="false">{{ errorMessage }}</Message>

      <div class="actions">
        <Button label="Back" severity="secondary" outlined :disabled="busy" @click="reset" />
        <Button
          :label="preview?.match_investor_id ? 'Import to this investor' : 'Create & import'"
          icon="pi pi-upload"
          :loading="busy"
          @click="submit(false)"
        />
      </div>
    </div>

    <!-- Destructive eCAS: preview removals, require explicit confirmation -->
    <div v-else-if="needsConfirmation" class="card">
      <Message severity="warn" :closable="false">
        This eCAS (dated {{ result.statement_date }}) no longer lists
        {{ result.removals?.length ?? 0 }} holding(s) you currently hold. Applying it will
        <strong>remove</strong> them.
      </Message>
      <ul class="removals">
        <li v-for="r in result.removals" :key="r.isin || r.name">
          <span class="sec-name">{{ r.name }}</span>
          <span class="muted mono">{{ r.units }} units</span>
        </li>
      </ul>
      <div class="actions">
        <Button label="Cancel" severity="secondary" outlined :disabled="busy" @click="reset" />
        <Button
          label="Remove & apply"
          icon="pi pi-trash"
          severity="danger"
          :loading="busy"
          @click="submit(true)"
        />
      </div>
    </div>

    <!-- Success / warnings -->
    <div v-else class="card">
      <Message v-if="failed" severity="error" :closable="false">{{ job?.error }}</Message>

      <template v-if="succeeded">
        <Message :severity="result.detected === 'ecas' ? 'info' : 'success'" :closable="false">
          <template v-if="result.detected === 'ecas'">{{ result.notice }}</template>
          <template v-else>Mutual-fund CAS imported as a transaction ledger.</template>
        </Message>

        <!-- MF CAS summary -->
        <dl v-if="result.detected === 'mf_cas'" class="summary">
          <div><dt>Schemes</dt><dd class="mono">{{ result.schemes ?? 0 }}</dd></div>
          <div><dt>Transactions added</dt><dd class="mono">{{ result.transactions_created ?? 0 }}</dd></div>
          <div v-if="result.transactions_skipped"><dt>Already on file</dt><dd class="mono">{{ result.transactions_skipped }}</dd></div>
          <div v-if="result.holdings_snapshotted"><dt>Snapshot-only schemes</dt><dd class="mono">{{ result.holdings_snapshotted }}</dd></div>
        </dl>

        <!-- eCAS summary -->
        <dl v-else class="summary">
          <div><dt>Demat accounts</dt><dd class="mono">{{ result.accounts ?? 0 }}</dd></div>
          <div><dt>Holdings added</dt><dd class="mono">{{ result.holdings_created ?? 0 }}</dd></div>
          <div v-if="result.holdings_updated"><dt>Holdings updated</dt><dd class="mono">{{ result.holdings_updated }}</dd></div>
          <div v-if="result.holdings_removed"><dt>Holdings removed</dt><dd class="mono">{{ result.holdings_removed }}</dd></div>
        </dl>

        <!-- Incomplete-history schemes: snapshotted, no cost-basis worksheet -->
        <div v-if="result.incomplete_history?.length" class="warn-block">
          <h2>Net worth only ({{ result.incomplete_history.length }})</h2>
          <p class="muted">
            These schemes were saved for net worth only — re-download a
            <strong>since-inception (Detailed) CAS</strong> and we can build a capital-gains
            worksheet for them.
          </p>
          <ul class="incomplete">
            <li v-for="s in result.incomplete_history" :key="`${s.security}-${s.folio}`">
              <span class="sec-name">{{ s.security }}</span>
              <span class="muted">folio {{ s.folio }}</span>
              <span class="reason">{{ reasonLabel(s.reason) }}</span>
            </li>
          </ul>
        </div>

        <Message v-if="result.reconcile_errors" severity="warn" :closable="false">
          Imported, but some securities couldn’t be reconciled — they may show as needing
          attention until the next import.
        </Message>
      </template>

      <div class="actions">
        <Button label="Import another" severity="secondary" outlined icon="pi pi-replay" @click="reset" />
        <Button v-if="succeeded" label="View dashboard" icon="pi pi-arrow-right" icon-pos="right" @click="goToDashboard" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.import-page {
  max-width: 40rem;
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
.scope-note {
  margin: var(--fm-space-3) 0 0;
  padding: var(--fm-space-3) var(--fm-space-4);
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
  background: var(--fm-surface-raised);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-md);
}
.mono {
  font-family: var(--fm-font-mono);
  font-variant-numeric: tabular-nums;
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

.actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--fm-space-2);
}

.summary {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: var(--fm-space-2) var(--fm-space-4);
  margin: 0;
}
.summary > div {
  display: contents;
}
.summary dt {
  color: var(--fm-text-muted);
}
.summary dd {
  margin: 0;
  text-align: right;
  font-weight: 600;
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
.incomplete,
.removals {
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
.removals li {
  display: flex;
  justify-content: space-between;
  gap: var(--fm-space-3);
}
.sec-name {
  font-weight: 500;
}
.reason {
  flex-basis: 100%;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
</style>
