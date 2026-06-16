<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Message from 'primevue/message'
import Select from 'primevue/select'
import RealisedGains from '@/components/RealisedGains.vue'
import { useCapitalGains } from '@/composables/useCapitalGains'
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

const {
  fy,
  fyOptions,
  includeUnreconciled,
  gains,
  report,
  loading,
  error,
  built,
  builtAt,
  worksheetRowCount,
  excluded,
  build,
} = useCapitalGains(investorId)

// A fresh build must be re-acknowledged before the worksheet can be downloaded.
const acknowledged = ref(false)
async function rebuild(): Promise<void> {
  acknowledged.value = false
  await build()
}
watch([investorId, fy, includeUnreconciled], () => void rebuild(), { immediate: true })

const canDownload = computed(() => acknowledged.value && worksheetRowCount.value > 0)

async function download(): Promise<void> {
  const r = report.value
  if (!r || !canDownload.value) return
  const saved = await downloadText(`capital-gains-worksheet-${r.fy}.csv`, toCsv(r.columns, r.rows))
  if (saved !== false) {
    ui.notify({ severity: 'success', summary: 'Worksheet downloaded for your review' })
  }
}

function back(): void {
  void router.push({ name: 'dashboard', params: { investorId: investorId.value } })
}
</script>

<template>
  <section class="cg-page">
    <header class="page-head">
      <button class="back" type="button" @click="back"><i class="pi pi-arrow-left" /> Dashboard</button>
      <div class="title-row">
        <h1>Capital Gains</h1>
      </div>
      <p class="subtitle">{{ investorName }} — realised gains for the year, for your review.</p>
    </header>

    <Message severity="info" :closable="false" class="coming-soon">
      Showing realised gains on <strong>mutual funds with full history</strong>. A comprehensive
      cross-asset tax statement — all asset classes, quarterly STCG/LTCG, crypto, and FD/dividend
      income — is coming once multi-asset support lands.
    </Message>

    <div class="controls">
      <label class="field">
        <span>Financial year</span>
        <Select v-model="fy" :options="fyOptions" :disabled="loading" size="small" />
      </label>
      <label class="check">
        <Checkbox v-model="includeUnreconciled" :binary="true" :disabled="loading" />
        <span>Include unreconciled folios <small>(may be wrong — review carefully)</small></span>
      </label>
    </div>

    <Message v-if="error" severity="error" :closable="false">
      Couldn’t compute capital gains for {{ fy }}. {{ error }}
    </Message>

    <RealisedGains
      v-if="built && !loading"
      :gains="gains"
      :excluded="excluded"
      :investor-id="investorId"
      :built-at="builtAt"
    />
    <div v-else-if="loading" class="cg-skeleton" aria-label="Computing capital gains" aria-busy="true">
      <div class="cg-skel-cards">
        <span class="fm-skeleton skel-card" />
        <span class="fm-skeleton skel-card" />
        <span class="fm-skeleton skel-card" />
      </div>
      <span class="fm-skeleton skel-head" />
      <span v-for="n in 5" :key="n" class="fm-skeleton skel-row" />
    </div>

    <Message v-if="gains?.disclaimer" severity="warn" :closable="false" class="disclaimer">
      {{ gains.disclaimer }}
    </Message>

    <section v-if="built && worksheetRowCount > 0" class="download">
      <h2>Schedule 112A worksheet (LTCG)</h2>
      <label class="ack">
        <Checkbox v-model="acknowledged" :binary="true" input-id="ack-draft" />
        <span>
          I understand this is a <strong>draft worksheet for my own review</strong> — not a filed
          return — and I’ll check every figure before I rely on it.
        </span>
      </label>
      <Button
        label="Download Schedule 112A (CSV)"
        icon="pi pi-download"
        :disabled="!canDownload"
        @click="download"
      />
      <p class="download-note">ITR Schedule 112A column layout — for your records and review.</p>
    </section>
  </section>
</template>

<style scoped>
.cg-page {
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

.coming-soon {
  font-size: 0.875rem;
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
/* Loading shimmer that sketches the realised-gains summary + table. */
.cg-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-3);
}
.cg-skel-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--fm-space-3);
  margin-bottom: var(--fm-space-2);
}
.skel-card {
  height: 4.5rem;
}
.skel-head {
  height: 2.25rem;
  width: 100%;
}
.skel-row {
  height: 2.5rem;
  width: 100%;
}
@media (max-width: 600px) {
  .cg-skel-cards {
    grid-template-columns: 1fr;
  }
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
.download h2 {
  margin: 0;
  font-size: 1.05rem;
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
