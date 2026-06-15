<script setup lang="ts">
import { onMounted, ref } from 'vue'
import Message from 'primevue/message'
import { api, type Schemas } from '@/api/client'
import { useWriteLock } from '@/composables/useWriteLock'
import { formatDate } from '@/utils/format'
import { importSummary } from '@/utils/jobs'
import JobStatusBadge from '@/components/JobStatusBadge.vue'

const { readOnly } = useWriteLock()

// Recent imports across all investors, so a past outcome / re-import is visible
// here. Reuses the advisor-wide /api/jobs endpoint; the full list (with valuation
// status) lives on the Settings → Jobs & valuation tab.
const recentImports = ref<Schemas['ImportJobSummaryOut'][]>([])
onMounted(async () => {
  const res = await api.GET('/api/jobs')
  if (res.data) recentImports.value = res.data.imports.slice(0, 5)
})

// Intent-first: the user picks WHAT they're importing, then we route to the right
// flow. CAS is file-first + PAN-resolved; the tradebook is investor-first +
// column-mapped — two genuinely different paths, not one dropzone.
interface Source {
  to: { name: string }
  icon: string
  accent: string
  title: string
  formats: string
  blurb: string
}
const sources: Source[] = [
  {
    to: { name: 'import-cas' },
    icon: 'pi pi-file-pdf',
    accent: 'var(--fm-asset-equity)',
    title: 'Consolidated statement',
    formats: 'CAS · eCAS · PDF',
    blurb:
      'Mutual funds from a CAMS/KFintech CAS, or demat holdings from an NSDL/CDSL eCAS. We detect which it is and find the investor by PAN.',
  },
  {
    to: { name: 'import-transactions' },
    icon: 'pi pi-file-excel',
    accent: 'var(--fm-asset-gold)',
    title: 'Stock tradebook',
    formats: 'CSV · XLSX',
    blurb:
      'Equity trades exported from your broker (Zerodha, Upstox, …). Map the columns once and we build a full cost-basis ledger with capital gains.',
  },
]
</script>

<template>
  <section class="hub">
    <header class="page-head">
      <h1>Import</h1>
      <p class="muted lede">What would you like to bring in?</p>
    </header>

    <Message v-if="readOnly" severity="info" :closable="false">
      Importing is disabled on this read-only demo. Explore the sample portfolios already loaded.
    </Message>

    <div class="sources">
      <RouterLink
        v-for="s in sources"
        :key="s.title"
        class="source"
        :style="{ '--accent': s.accent }"
        :to="s.to"
      >
        <span class="source-icon"><i :class="s.icon" aria-hidden="true" /></span>
        <span class="source-body">
          <span class="source-top">
            <span class="source-title">{{ s.title }}</span>
            <span class="source-formats">{{ s.formats }}</span>
          </span>
          <span class="source-blurb muted">{{ s.blurb }}</span>
        </span>
        <i class="pi pi-arrow-right source-go" aria-hidden="true" />
      </RouterLink>
    </div>

    <p class="more muted">More brokers and asset types are on the way.</p>

    <!-- Recent imports (across both paths): newest first, advisor-wide. -->
    <section v-if="recentImports.length" class="recent">
      <header class="recent-head">
        <h2>Recent imports</h2>
        <RouterLink class="recent-all" :to="{ name: 'settings', params: { tab: 'jobs' } }">
          View all in Settings ↗
        </RouterLink>
      </header>
      <ul class="recent-list">
        <li v-for="j in recentImports" :key="j.id" class="recent-row">
          <span class="r-main">
            <span class="r-name">{{ j.filename || j.kind.toUpperCase() }}</span>
            <span class="r-sub">{{ j.investor_name }} · {{ formatDate(j.created_at) }}</span>
          </span>
          <span class="r-detail" :class="{ 'is-error': !!j.error }">{{ importSummary(j) }}</span>
          <JobStatusBadge :status="j.status" />
        </li>
      </ul>
    </section>
  </section>
</template>

<style scoped>
.hub {
  max-width: 44rem;
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
.lede {
  margin: 0;
}

.sources {
  display: grid;
  gap: var(--fm-space-3);
  margin-top: var(--fm-space-4);
}
.source {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: flex-start;
  gap: var(--fm-space-4);
  padding: var(--fm-space-5);
  /* White surface lifts off the off-white ground; surface-raised was too close
     to the canvas to read as a card. */
  background: var(--fm-surface);
  border: 1px solid var(--fm-border);
  border-radius: var(--fm-radius-lg);
  box-shadow: var(--fm-shadow-sm);
  text-decoration: none;
  color: inherit;
  transition:
    border-color var(--fm-dur) var(--fm-ease),
    box-shadow var(--fm-dur) var(--fm-ease),
    transform var(--fm-dur) var(--fm-ease);
}
/* Per-source colored spine (equity teal / gold) ties each card to its icon
   accent and gives the two options a distinct edge. */
.source::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 4px;
  background: var(--accent);
}
.source:hover {
  border-color: color-mix(in oklab, var(--accent) 55%, var(--fm-border));
  box-shadow: var(--fm-shadow-md);
  transform: translateY(-1px);
}
.source-icon {
  display: grid;
  place-items: center;
  flex: none;
  width: 2.75rem;
  height: 2.75rem;
  border-radius: var(--fm-radius-md);
  background: color-mix(in oklab, var(--accent) 14%, transparent);
  color: var(--accent);
}
.source-icon .pi {
  font-size: 1.35rem;
}
.source-body {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  flex: 1;
  min-width: 0;
}
.source-top {
  display: flex;
  align-items: baseline;
  gap: var(--fm-space-3);
  flex-wrap: wrap;
}
.source-title {
  font-size: 1.0625rem;
  font-weight: 600;
}
.source-formats {
  font-family: var(--fm-font-mono);
  font-size: 0.6875rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--fm-text-subtle);
}
.source-blurb {
  font-size: 0.8125rem;
  line-height: 1.5;
}
.source-go {
  align-self: center;
  flex: none;
  color: var(--fm-text-subtle);
  transition: transform var(--fm-dur) var(--fm-ease);
}
.source:hover .source-go {
  transform: translateX(3px);
  color: var(--accent);
}

.more {
  margin: var(--fm-space-4) 0 0;
  font-size: 0.75rem;
}

.recent {
  margin-top: var(--fm-space-6);
}
.recent-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--fm-space-3);
  margin-bottom: var(--fm-space-2);
}
.recent-head h2 {
  margin: 0;
  font-size: 0.8125rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--fm-text-muted);
}
.recent-all {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--p-primary-color);
  text-decoration: none;
  white-space: nowrap;
}
.recent-all:hover {
  text-decoration: underline;
}
.recent-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.recent-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--fm-space-3);
  padding: 0.55rem 0;
  border-top: 1px solid var(--fm-border-subtle);
}
.recent-row:first-child {
  border-top: none;
}
.r-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1 1 12rem;
}
.r-name {
  font-size: 0.875rem;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.r-sub {
  font-size: 0.75rem;
  color: var(--fm-text-muted);
}
.r-detail {
  flex: 1 1 8rem;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
  text-align: right;
}
.r-detail.is-error {
  color: var(--p-red-500, #ef4444);
}
</style>
