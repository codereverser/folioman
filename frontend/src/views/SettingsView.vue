<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import SelectButton from 'primevue/selectbutton'
import Button from 'primevue/button'
import { api, type Schemas } from '@/api/client'
import { useUiStore, type ThemePreference } from '@/stores/ui'
import { useRosterStore } from '@/stores/roster'
import { downloadText } from '@/utils/csv'

// Support routes through the public repo (issues / discussions); each row below
// renders only when configured.
const SUPPORT_URL = 'https://github.com/codereverser/folioman'
const SPONSOR_LINKS: { label: string; url: string }[] = [
  { label: 'GitHub Sponsors', url: 'https://github.com/sponsors/codereverser' },
  { label: 'Buy Me a Coffee', url: 'https://buymeacoffee.com/codereverser' },
]

const ui = useUiStore()
const roster = useRosterStore()

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

const isLocal = computed(() => meta.value?.storage === 'local')

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
          Folioman is local-first. Your CAS statements and portfolio data live only where this app
          runs — there's no account, no cloud sync, and no analytics or tracking. PAN numbers are
          encrypted at rest and are never shown in full.
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
          <code v-if="isLocal && meta.data_location" class="path">{{ meta.data_location }}</code>
          <p v-if="isLocal" class="hint">
            <strong>Backup:</strong> copy that file somewhere safe. To restore, put it back before
            launching Folioman. That one file is your whole portfolio.
          </p>
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
.hint {
  margin-top: 0.5rem !important;
}

.actions {
  display: flex;
  gap: var(--fm-space-2);
  flex-shrink: 0;
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

@media (max-width: 600px) {
  .setting {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
