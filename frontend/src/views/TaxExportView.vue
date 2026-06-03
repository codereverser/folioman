<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Message from 'primevue/message'
import Select from 'primevue/select'
import ExportPreview from '@/components/ExportPreview.vue'
import { useTaxExport } from '@/composables/useTaxExport'
import { useRosterStore } from '@/stores/roster'
import { useUiStore } from '@/stores/ui'
import { toCsv, downloadText } from '@/utils/csv'

const route = useRoute()
const router = useRouter()
const roster = useRosterStore()
const ui = useUiStore()

const investorId = computed(() => {
  const raw = route.params.investorId
  const n = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(n) ? n : (ui.selectedInvestorId ?? 0)
})
const investorName = computed(() => roster.investorName(investorId.value) ?? 'Investor')

const { fy, fyOptions, includeUnreconciled, report, loading, error, built, rowCount, excluded, build } =
  useTaxExport(investorId)

// A fresh worksheet must be re-acknowledged before it can be downloaded.
const acknowledged = ref(false)
async function rebuild(): Promise<void> {
  acknowledged.value = false
  await build()
}
watch([investorId, fy, includeUnreconciled], () => void rebuild(), { immediate: true })

const title = computed(() => report.value?.title ?? 'Capital-gains worksheet')
const canDownload = computed(() => acknowledged.value && rowCount.value > 0)

function download(): void {
  const r = report.value
  if (!r || !canDownload.value) return
  const csv = toCsv(r.columns, r.rows)
  downloadText(`capital-gains-worksheet-${r.fy}.csv`, csv)
  ui.notify({ severity: 'success', summary: 'Worksheet downloaded for your review' })
}

function back(): void {
  void router.push({ name: 'dashboard', params: { investorId: investorId.value } })
}
</script>

<template>
  <section class="tax-page">
    <header class="page-head">
      <button class="back" type="button" @click="back"><i class="pi pi-arrow-left" /> Dashboard</button>
      <div class="title-row">
        <h1>{{ title }}</h1>
      </div>
      <p class="subtitle">{{ investorName }} — a draft for your review, not a filed return.</p>
    </header>

    <div class="controls">
      <label class="field">
        <span>Financial year</span>
        <Select v-model="fy" :options="fyOptions" :disabled="loading" size="small" />
      </label>
      <label class="check">
        <Checkbox v-model="includeUnreconciled" :binary="true" :disabled="loading" />
        <span>Include unreconciled folios <small>(adds rows that may be wrong — review carefully)</small></span>
      </label>
    </div>

    <Message v-if="error" severity="error" :closable="false">
      Couldn’t build the worksheet for {{ fy }}. {{ error }}
    </Message>

    <Message v-if="report?.disclaimer" severity="warn" :closable="false" class="disclaimer">
      {{ report.disclaimer }}
    </Message>

    <ExportPreview
      v-if="built && !loading"
      :report="report"
      :excluded="excluded"
      :investor-id="investorId"
    />
    <p v-else-if="loading" class="loading-note">Building worksheet…</p>

    <section v-if="built && rowCount > 0" class="download">
      <label class="ack">
        <Checkbox v-model="acknowledged" :binary="true" input-id="ack-draft" />
        <span>
          I understand this is a <strong>draft worksheet for my own review</strong> — not a filed
          return — and I’ll check every figure before I rely on it.
        </span>
      </label>
      <Button
        label="Download worksheet (CSV)"
        icon="pi pi-download"
        :disabled="!canDownload"
        @click="download"
      />
      <p class="download-note">ITR Schedule 112A column layout — for your records and review.</p>
    </section>
  </section>
</template>

<style scoped>
.tax-page {
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
.title-row h1 {
  margin: 0;
}
.subtitle {
  margin: 0;
  color: var(--fm-text-muted);
  font-size: 0.9375rem;
}

.controls {
  display: flex;
  align-items: center;
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
.check {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
}
.check small {
  color: var(--fm-text-subtle);
}

.disclaimer {
  font-size: 0.8125rem;
}
.loading-note {
  color: var(--fm-text-muted);
}

.download {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-3);
  padding: var(--fm-space-5);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
  background: var(--fm-surface);
}
.ack {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
  font-size: 0.875rem;
  line-height: 1.45;
}
.download :deep(.p-button) {
  align-self: flex-start;
}
.download-note {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--fm-text-subtle);
}
</style>
