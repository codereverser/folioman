<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SelectButton from 'primevue/selectbutton'
import Button from 'primevue/button'
import { api, type Schemas } from '@/api/client'
import { useUiStore, type ThemePreference } from '@/stores/ui'
import { useAuthStore } from '@/stores/auth'
import { useRosterStore } from '@/stores/roster'
import { downloadText } from '@/utils/csv'
import { formatDate } from '@/utils/format'
import { importSummary } from '@/utils/jobs'
import JobStatusBadge from '@/components/JobStatusBadge.vue'

const route = useRoute()
// Settings is split into tabs (route param ":tab"); bare /settings = general.
const activeTab = computed<'general' | 'jobs'>(() =>
  route.params.tab === 'jobs' ? 'jobs' : 'general',
)

// Support routes through the public repo (issues / discussions); each row below
// renders only when configured.
const SUPPORT_URL = 'https://github.com/codereverser/folioman'
const SPONSOR_LINKS: { label: string; url: string }[] = [
  { label: 'GitHub Sponsors', url: 'https://github.com/sponsors/codereverser' },
  { label: 'Buy Me a Coffee', url: 'https://buymeacoffee.com/codereverser' },
]

const ui = useUiStore()
const auth = useAuthStore()
const roster = useRosterStore()
const router = useRouter()

// The Account card (sign out) is server-mode only: in desktop/local mode there's
// no login, so the auth store never holds a token.
const showAccount = computed(() => auth.isAuthenticated)
function signOut(): void {
  auth.logout()
  void router.push({ name: 'login' })
}

const themeOptions: { label: string; value: ThemePreference; icon: string }[] = [
  { label: 'Light', value: 'light', icon: 'pi pi-sun' },
  { label: 'Dark', value: 'dark', icon: 'pi pi-moon' },
  { label: 'System', value: 'system', icon: 'pi pi-desktop' },
]

const meta = ref<Schemas['AppMetaOut'] | null>(null)
onMounted(async () => {
  const res = await api.GET('/api/meta')
  if (res.data) meta.value = res.data
})

// Jobs & valuation activity (advisor-wide): recent imports + per-investor valuation
// status with the real per-security cause of any failure.
type Diagnostics = Schemas['ValuationDiagnosticsOut']

const jobs = ref<Schemas['JobsOverviewOut'] | null>(null)
const jobsLoading = ref(false)
let jobsRequested = false
// Lazy: only hit /api/jobs once the Jobs tab is actually opened (it can get big).
async function loadJobs(): Promise<void> {
  if (jobsRequested) return
  jobsRequested = true
  jobsLoading.value = true
  try {
    const res = await api.GET('/api/jobs')
    if (res.data) jobs.value = res.data
  } finally {
    jobsLoading.value = false
  }
}
watch(activeTab, (tab) => tab === 'jobs' && void loadJobs(), { immediate: true })

// Only investors that need attention (not ready, or ready-but-degraded with issues).
const problemValuations = computed<Diagnostics[]>(() =>
  (jobs.value?.valuations ?? []).filter((v) => v.status !== 'ready' || (v.issues?.length ?? 0) > 0),
)

const isLocal = computed(() => meta.value?.storage === 'local')

async function copyPath(text: string | null | undefined): Promise<void> {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    ui.notify({ severity: 'success', summary: 'Copied', detail: 'Path copied to clipboard.', life: 3000 })
  } catch {
    ui.notify({ severity: 'error', summary: 'Copy failed', detail: 'Could not copy path to clipboard.', life: 3000 })
  }
}

// Exports are per-investor; enabled once a single investor is in scope.
const investorId = computed(() => ui.selectedInvestorId)
const investorName = computed(() =>
  investorId.value != null ? (roster.investorName(investorId.value) ?? 'this investor') : null,
)

const exporting = ref<'holdings' | 'transactions' | null>(null)

async function exportHoldings(): Promise<void> {
  const id = investorId.value
  if (id == null) return
  exporting.value = 'holdings'
  try {
    const res = await api.GET('/api/investors/{investor_id}/exports/holdings', {
      params: { path: { investor_id: id } },
      parseAs: 'text',
    })
    if (typeof res.data === 'string') downloadText(`holdings_${id}.csv`, res.data)
    else
      ui.notify({ severity: 'error', summary: 'Export failed', detail: 'Could not build the holdings CSV.' })
  } finally {
    exporting.value = null
  }
}

async function exportTransactions(): Promise<void> {
  const id = investorId.value
  if (id == null) return
  exporting.value = 'transactions'
  try {
    const res = await api.GET('/api/investors/{investor_id}/exports/transactions', {
      params: { path: { investor_id: id } },
      parseAs: 'text',
    })
    if (typeof res.data === 'string') downloadText(`transactions_${id}.csv`, res.data)
    else
      ui.notify({
        severity: 'error',
        summary: 'Export failed',
        detail: 'Could not build the transactions CSV.',
      })
  } finally {
    exporting.value = null
  }
}
</script>

<template>
  <section class="settings">
    <header class="page-head">
      <h1>Settings</h1>
      <p class="sub">Appearance, your data, and privacy — all on this device.</p>
    </header>

    <nav class="tabs" aria-label="Settings sections">
      <RouterLink
        class="tab"
        :class="{ active: activeTab === 'general' }"
        :to="{ name: 'settings' }"
        >General</RouterLink
      >
      <RouterLink
        class="tab"
        :class="{ active: activeTab === 'jobs' }"
        :to="{ name: 'settings', params: { tab: 'jobs' } }"
        >Jobs &amp; valuation</RouterLink
      >
    </nav>

    <!-- General settings -->
    <template v-if="activeTab === 'general'">
      <!-- Appearance -->
      <article class="card setting">
        <div class="setting-text">
          <h2>Appearance</h2>
          <p>Choose a theme, or follow your system.</p>
        </div>
        <SelectButton
          :model-value="ui.themePreference"
          :options="themeOptions"
          option-label="label"
          option-value="value"
          :allow-empty="false"
          @update:model-value="(v: ThemePreference | null) => v && ui.setTheme(v)"
        >
          <template #option="{ option }">
            <i :class="option.icon" />
            <span class="opt-label">{{ option.label }}</span>
          </template>
        </SelectButton>
      </article>

      <!-- Privacy -->
      <article class="card setting block">
        <div class="setting-text">
          <h2><i class="pi pi-lock" /> Privacy</h2>
          <p>
            Folioman is local-first. Your CAS statements and portfolio data live only where this
            app runs — there's no account, no cloud sync, and no analytics or tracking. PAN numbers
            are encrypted at rest and are never shown in full.
          </p>
        </div>
      </article>

      <!-- Data location + backup -->
      <article class="card setting block">
        <div class="setting-text">
          <h2><i class="pi pi-database" /> Your data</h2>
          <template v-if="meta">
            <p v-if="isLocal">
              Stored locally on this device. Your entire portfolio is a single database file:
            </p>
            <p v-else>
              Stored in the hosted Folioman database (managed and backed up on the server).
            </p>
            <code
              v-if="isLocal && meta.data_location"
              class="path copyable"
              title="Click to copy path"
              @click="copyPath(meta.data_location)"
            >{{ meta.data_location }}</code>
            <p v-if="isLocal" class="hint">
              <strong>Backup:</strong> copy that file somewhere safe. To restore, put it back before
              launching Folioman. That one file is your whole portfolio.
            </p>
            <template v-if="isLocal && meta.key_location">
              <p class="hint">
                <strong>Encryption key:</strong> your PANs are encrypted at rest with a key stored
                separately, here:
              </p>
              <code
                class="path copyable"
                title="Click to copy path"
                @click="copyPath(meta.key_location)"
              >{{ meta.key_location }}</code>
              <p class="hint">
                Back this up too — without it, encrypted PANs can't be recovered. Keep it somewhere
                different from the database file.
              </p>
            </template>
          </template>
          <p v-else class="hint">Loading…</p>
        </div>
      </article>

      <!-- Export -->
      <article class="card setting">
        <div class="setting-text">
          <h2><i class="pi pi-download" /> Export</h2>
          <p v-if="investorName">
            Download <strong>{{ investorName }}</strong
            >'s data as CSV. (Capital-gains &amp; Schedule 112A exports live on the Capital Gains
            screen.)
          </p>
          <p v-else class="hint">Pick an investor from the switcher to export their data.</p>
        </div>
        <div class="actions">
          <Button
            label="Holdings"
            icon="pi pi-file"
            severity="secondary"
            outlined
            size="small"
            :disabled="investorId == null"
            :loading="exporting === 'holdings'"
            @click="exportHoldings"
          />
          <Button
            label="Transactions"
            icon="pi pi-list"
            severity="secondary"
            outlined
            size="small"
            :disabled="investorId == null"
            :loading="exporting === 'transactions'"
            @click="exportTransactions"
          />
        </div>
      </article>

      <!-- Account (server mode only) -->
      <article v-if="showAccount" class="card setting">
        <div class="setting-text">
          <h2><i class="pi pi-user" /> Account</h2>
          <p>Signed in to the hosted Folioman server.</p>
        </div>
        <div class="actions">
          <Button
            class="signout"
            label="Sign out"
            icon="pi pi-sign-out"
            severity="secondary"
            outlined
            size="small"
            @click="signOut"
          />
        </div>
      </article>

      <!-- About / support -->
      <article class="card setting block">
        <div class="setting-text">
          <h2><i class="pi pi-info-circle" /> About</h2>
          <p class="kv"><span>Version</span><strong>{{ meta?.version ?? '—' }}</strong></p>
          <p v-if="SUPPORT_URL" class="kv">
            <span>Help &amp; issues</span>
            <a :href="SUPPORT_URL" target="_blank" rel="noopener noreferrer">GitHub repository ↗</a>
          </p>
          <p v-if="SPONSOR_LINKS.length" class="kv">
            <span>Support development</span>
            <span class="links">
              <a
                v-for="link in SPONSOR_LINKS"
                :key="link.url"
                :href="link.url"
                target="_blank"
                rel="noopener noreferrer"
                >{{ link.label }} ↗</a
              >
            </span>
          </p>
        </div>
      </article>
    </template>

    <!-- Jobs & valuation activity -->
    <template v-else>
      <p v-if="jobsLoading" class="hint">Loading…</p>
      <template v-else-if="jobs">
        <article class="card setting block">
          <div class="setting-text">
            <h2><i class="pi pi-inbox" /> Recent imports</h2>
            <p>What's been imported across all your investors, newest first.</p>
          </div>
          <p v-if="!jobs.imports.length" class="hint">Nothing imported yet.</p>
          <ul v-else class="jobs-list">
            <li v-for="job in jobs.imports" :key="job.id" class="job-row">
              <span class="job-main">
                <span class="job-name">{{ job.filename || job.kind.toUpperCase() }}</span>
                <span class="job-sub"
                  >{{ job.investor_name }} · {{ formatDate(job.created_at) }}</span
                >
              </span>
              <span class="job-detail" :class="{ 'is-error': !!job.error }">{{
                importSummary(job)
              }}</span>
              <JobStatusBadge :status="job.status" />
            </li>
          </ul>
        </article>

        <article class="card setting block">
          <div class="setting-text">
            <h2><i class="pi pi-chart-line" /> Valuation status</h2>
            <p>Whether each portfolio's day-wise valuation is current, and why if not.</p>
          </div>
          <p v-if="!problemValuations.length" class="hint">
            All portfolios are valued and up to date.
          </p>
          <ul v-else class="jobs-list">
            <li v-for="v in problemValuations" :key="v.investor_id" class="job-row val">
              <span class="job-main">
                <span class="job-name">{{ v.investor_name }}</span>
                <span v-if="v.computed_through" class="job-sub"
                  >valued through {{ formatDate(v.computed_through) }}</span
                >
              </span>
              <JobStatusBadge :status="v.status" />
              <ul v-if="v.issues?.length" class="issues">
                <li v-for="iss in v.issues ?? []" :key="iss.security_id">
                  <span class="cause" :class="iss.cause">{{ iss.security_name }}</span>
                  <span class="cause-detail">{{ iss.detail }}</span>
                </li>
              </ul>
            </li>
          </ul>
        </article>
      </template>
    </template>
  </section>
</template>

<style scoped>
.settings {
  max-width: 48rem;
  margin: 0 auto;
  padding: var(--fm-space-6);
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-4);
}
.page-head {
  margin-bottom: var(--fm-space-2);
}
.page-head h1 {
  margin: 0;
  font-size: 1.5rem;
}
.page-head .sub {
  margin: 0.25rem 0 0;
  color: var(--fm-text-muted);
}

/* Sub-tab strip (General / Jobs & valuation) */
.tabs {
  display: flex;
  gap: var(--fm-space-1, 0.25rem);
  border-bottom: 1px solid var(--fm-border-subtle);
  margin-bottom: var(--fm-space-2);
  overflow-x: auto;
}
.tab {
  padding: 0.6rem 0.95rem;
  text-decoration: none;
  white-space: nowrap;
  color: var(--fm-text-muted);
  font-weight: 600;
  font-size: 0.9rem;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color var(--fm-dur-fast) var(--fm-ease);
}
.tab:hover {
  color: var(--fm-text);
}
.tab.active {
  color: var(--p-primary-color);
  border-bottom-color: var(--p-primary-color);
}

.card {
  background: var(--fm-surface);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
  padding: var(--fm-space-5);
}
/* Row layout: label/description on the left, control on the right. */
.setting {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fm-space-5);
}
.setting.block {
  flex-direction: column;
  align-items: stretch;
}
.setting-text h2 {
  margin: 0 0 0.25rem;
  font-size: 1rem;
  display: flex;
  align-items: center;
  gap: 0.45rem;
}
.setting-text h2 i {
  color: var(--fm-text-muted);
  font-size: 0.95rem;
}
.setting-text p {
  margin: 0;
  color: var(--fm-text-muted);
  font-size: 0.875rem;
  line-height: 1.5;
}
.opt-label {
  margin-left: 0.4rem;
}

.path {
  display: block;
  margin: 0.6rem 0;
  padding: 0.55rem 0.7rem;
  background: var(--fm-surface-raised);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-sm);
  font-family: var(--fm-font-mono, monospace);
  font-size: 0.8125rem;
  color: var(--fm-text);
  word-break: break-all;
}
.path.copyable {
  cursor: pointer;
  transition: background-color var(--fm-dur-fast) var(--fm-ease);
}
.path.copyable:hover {
  background: var(--fm-surface-overlay);
}
.hint {
  margin-top: 0.5rem !important;
}

.actions {
  display: flex;
  gap: var(--fm-space-2);
  flex-shrink: 0;
}
/* Sign out: same neutral palette as the export buttons, just a touch more
   present — a faint filled surface behind the outline so it reads as solid. */
.actions :deep(.signout) {
  background: var(--fm-surface-raised);
}
.actions :deep(.signout:hover) {
  background: var(--fm-surface-overlay);
}

.kv {
  display: flex;
  justify-content: space-between;
  gap: var(--fm-space-4);
  padding: 0.35rem 0;
  border-top: 1px solid var(--fm-border-subtle);
}
.kv:first-of-type {
  border-top: none;
}
.kv span {
  color: var(--fm-text-muted);
}
.kv strong,
.kv a {
  color: var(--fm-text);
  font-weight: 600;
}
.kv a {
  color: var(--p-primary-color);
  text-decoration: none;
}
.kv a:hover {
  text-decoration: underline;
}
.links {
  display: flex;
  gap: var(--fm-space-4);
  flex-wrap: wrap;
  justify-content: flex-end;
}

/* Jobs & valuation activity */
.jobs-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.job-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--fm-space-3);
  padding: 0.55rem 0;
  border-top: 1px solid var(--fm-border-subtle);
}
.job-row:first-child {
  border-top: none;
}
.job-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1 1 12rem;
}
.job-name {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--fm-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.job-sub {
  font-size: 0.75rem;
  color: var(--fm-text-muted);
}
.job-detail {
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
  text-align: right;
  flex: 1 1 8rem;
}
.job-detail.is-error {
  color: var(--p-red-500, #ef4444);
}

/* Per-security valuation issues (the real cause), full-width under the row */
.issues {
  flex-basis: 100%;
  list-style: none;
  margin: 0.4rem 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.issues li {
  font-size: 0.75rem;
  line-height: 1.4;
}
.issues .cause {
  font-weight: 600;
  color: var(--fm-text);
  margin-right: 0.4rem;
}
.issues .cause::before {
  content: '';
  display: inline-block;
  width: 0.45rem;
  height: 0.45rem;
  border-radius: 50%;
  margin-right: 0.35rem;
  vertical-align: middle;
  background: var(--fm-text-muted);
}
.issues .cause.closed::before {
  background: var(--p-amber-500, #f59e0b);
}
.issues .cause.unmapped::before {
  background: var(--p-red-500, #ef4444);
}
.issues .cause.feed_pending::before {
  background: var(--p-blue-500, #3b82f6);
}
.issues .cause-detail {
  color: var(--fm-text-muted);
}

@media (max-width: 600px) {
  .setting {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
