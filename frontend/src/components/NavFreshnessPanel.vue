<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import InputText from 'primevue/inputtext'
import SelectButton from 'primevue/selectbutton'
import { api, type Schemas } from '@/api/client'
import { formatDate } from '@/utils/format'
import { assetLabel } from '@/utils/portfolio'

type FreshnessRow = Schemas['NavSecurityFreshnessOut']

const loading = ref(true)
const freshness = ref<Schemas['NavFreshnessOut'] | null>(null)

async function load(): Promise<void> {
  const res = await api.GET('/api/navs/freshness')
  if (res.data) freshness.value = res.data
  loading.value = false
}
onMounted(() => void load())

// Severity scale: teal current, rose stale; informational blues/greys for the
// unpriceable tail. Lags are measured against the last *completed* trading day
// (a NAV publishes after its trading day ends), so "fresh" really is current.
const STATUS_META: Record<string, { label: string; color: string }> = {
  fresh: { label: 'Current', color: 'var(--fm-verified)' },
  grace: { label: '1 day behind', color: 'var(--fm-warn)' },
  stale: { label: 'Stale', color: 'var(--fm-critical)' },
  pending: { label: 'Awaiting first price', color: 'var(--p-blue-500, #3b82f6)' },
  closed: { label: 'Feed closed', color: 'var(--p-amber-500, #f59e0b)' },
  no_feed: { label: 'No feed', color: 'var(--fm-text-muted)' },
}
const statusLabel = (s: string) => STATUS_META[s]?.label ?? s
const statusColor = (s: string) => STATUS_META[s]?.color ?? 'var(--fm-text-muted)'

const rows = computed<FreshnessRow[]>(() => freshness.value?.securities ?? [])
const total = computed(() => rows.value.length)
const currentCount = computed(() => rows.value.filter((r) => r.status === 'fresh').length)

// Status filter (like the Integrity page): All / needs-attention slices.
const FILTERS = [
  { label: 'All', value: 'all' },
  { label: 'Stale', value: 'stale' },
  { label: 'Behind', value: 'grace' },
  { label: 'Closed', value: 'closed' },
  { label: 'Unpriced', value: 'unpriced' }, // pending + no_feed
  { label: 'Current', value: 'fresh' },
]
const filter = ref('all')
// Free-text search across name + identifier: the common case is looking up a
// handful of specific securities, not reading the whole list.
const query = ref('')
const filtered = computed<FreshnessRow[]>(() => {
  let list = rows.value
  if (filter.value === 'unpriced')
    list = list.filter((r) => r.status === 'pending' || r.status === 'no_feed')
  else if (filter.value !== 'all') list = list.filter((r) => r.status === filter.value)
  const q = query.value.trim().toLowerCase()
  if (q)
    list = list.filter(
      (r) => r.name.toLowerCase().includes(q) || r.identifier.toLowerCase().includes(q),
    )
  return list
})

// Group by asset class (Mutual funds / Stocks / Bonds / …); rows inside keep the
// backend's worst-first order. Group order follows worst row in each group so
// the section needing attention surfaces on top. Each group renders capped
// (worst rows first, so nothing urgent hides behind the fold) and grows in
// fixed increments — search is the intended way to reach a specific security,
// and incremental growth keeps a thousands-row group from flooding the DOM in
// one flush.
const GROUP_CAP = 15
const GROUP_STEP = 50
const visibleByType = ref(new Map<string, number>())
function showMore(type: string): void {
  const next = new Map(visibleByType.value)
  next.set(type, (next.get(type) ?? GROUP_CAP) + GROUP_STEP)
  visibleByType.value = next
}
function showLess(type: string): void {
  const next = new Map(visibleByType.value)
  next.delete(type)
  visibleByType.value = next
}
// Re-collapse when the slice changes — the cap is per filtered view.
watch([filter, query], () => (visibleByType.value = new Map()))

const groups = computed(() => {
  const byType = new Map<string, FreshnessRow[]>()
  for (const r of filtered.value) {
    const list = byType.get(r.security_type) ?? []
    list.push(r)
    byType.set(r.security_type, list)
  }
  const severity = { stale: 0, pending: 1, closed: 2, grace: 3, no_feed: 4, fresh: 5 }
  const worst = (list: FreshnessRow[]) =>
    Math.min(...list.map((r) => severity[r.status as keyof typeof severity] ?? 9))
  return [...byType.entries()]
    .map(([type, list]) => {
      const visible = visibleByType.value.get(type) ?? GROUP_CAP
      return {
        type,
        label: assetLabel(type),
        total: list.length,
        rows: list.slice(0, visible),
        remaining: Math.max(0, list.length - visible),
        grown: visible > GROUP_CAP,
      }
    })
    .sort((a, b) => {
      const all = (g: { type: string }) => byType.get(g.type)!
      return worst(all(a)) - worst(all(b)) || a.label.localeCompare(b.label)
    })
})

// Segmented spectrum: one proportional band per status, worst-first; "current"
// sits at the right end.
const spectrum = computed(() => {
  const order = ['stale', 'pending', 'closed', 'no_feed', 'grace', 'fresh']
  const counts = new Map<string, number>()
  for (const r of rows.value) counts.set(r.status, (counts.get(r.status) ?? 0) + 1)
  return order
    .filter((s) => counts.has(s))
    .map((s) => ({
      status: s,
      count: counts.get(s)!,
      pct: (counts.get(s)! / total.value) * 100,
    }))
})

function lagText(row: FreshnessRow): string {
  if (row.lag_trading_days == null || row.lag_trading_days === 0) return ''
  if (row.lag_trading_days === 1) return '1 trading day'
  return `${row.lag_trading_days} trading days`
}

// The schedule's absolute instants render in the viewer's local clock.
const timeFmt = new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit' })
const nextRefreshText = computed(() => {
  const iso = freshness.value?.next_refresh_at
  if (!iso) return ''
  const d = new Date(iso)
  const today = new Date()
  const sameDay = d.toDateString() === today.toDateString()
  return sameDay ? `today at ${timeFmt.format(d)}` : `tomorrow at ${timeFmt.format(d)}`
})
const lastRefreshText = computed(() => {
  const iso = freshness.value?.last_refreshed_at
  if (!iso) return 'never'
  const d = new Date(iso)
  return `${formatDate(iso)}, ${timeFmt.format(d)}`
})
</script>

<template>
  <article class="card nav-panel">
    <div class="panel-head">
      <div class="setting-text">
        <h2><i class="pi pi-wave-pulse" /> NAV freshness</h2>
        <p>
          How current each tracked security's price is, against the last completed trading day
          <strong v-if="freshness">({{ formatDate(freshness.as_of) }})</strong>.
        </p>
      </div>
    </div>

    <p v-if="loading" class="hint">Loading…</p>
    <template v-else-if="total">
      <!-- Freshness spectrum: the whole book in one glance -->
      <div class="spectrum-wrap">
        <p class="spectrum-headline">
          <strong>{{ currentCount }} of {{ total }}</strong> securities current
        </p>
        <div class="spectrum" aria-hidden="true">
          <div
            v-for="seg in spectrum"
            :key="seg.status"
            class="seg"
            :style="{ width: `${seg.pct}%`, background: statusColor(seg.status) }"
          />
        </div>
        <ul class="legend">
          <li v-for="seg in spectrum" :key="seg.status">
            <span class="dot" :style="{ background: statusColor(seg.status) }" />
            {{ statusLabel(seg.status) }} · {{ seg.count }}
          </li>
        </ul>
      </div>

      <!-- Refresh schedule: prices update themselves; no user trigger -->
      <p class="schedule" role="status">
        <i class="pi pi-clock" />
        <span>
          Prices refresh automatically every 6 hours — next run
          <strong>{{ nextRefreshText }}</strong> · last updated {{ lastRefreshText }}.
        </span>
      </p>

      <div class="toolbar">
        <SelectButton
          v-model="filter"
          :options="FILTERS"
          option-label="label"
          option-value="value"
          :allow-empty="false"
          size="small"
          aria-label="Filter securities by freshness"
        />
        <span class="search">
          <i class="pi pi-search" />
          <InputText
            v-model="query"
            placeholder="Search name / ISIN / symbol"
            size="small"
            aria-label="Search securities"
          />
        </span>
      </div>

      <p v-if="!filtered.length" class="hint">Nothing in this slice.</p>
      <div v-else class="groups">
        <section v-for="g in groups" :key="g.type" class="type-group">
          <header class="group-head">
            <h3>{{ g.label }}</h3>
            <small>{{ g.total }}</small>
          </header>
          <div class="rows" role="table" aria-label="Securities">
            <div class="row head" role="row">
              <span role="columnheader">Security</span>
              <span role="columnheader">Latest NAV</span>
              <span role="columnheader">Status</span>
              <span role="columnheader">History</span>
            </div>
            <div v-for="row in g.rows" :key="row.security_id" class="row" role="row">
              <span class="cell-sec" role="cell">
                <span class="sec-name">{{ row.name }}</span>
                <span v-if="row.identifier" class="sec-sub">{{ row.identifier }}</span>
              </span>
              <span class="mono" role="cell">{{
                row.latest_nav_date ? formatDate(row.latest_nav_date) : '—'
              }}</span>
              <span role="cell">
                <span class="lag-chip" :style="{ '--chip': statusColor(row.status) }">
                  <span class="dot" />{{ statusLabel(row.status) }}
                </span>
                <span v-if="lagText(row) && row.status !== 'grace'" class="sec-sub">{{
                  lagText(row)
                }}</span>
              </span>
              <span role="cell">
                <span v-if="row.points" class="mono sec-sub"
                  >{{ formatDate(row.first_nav_date) }} → {{ formatDate(row.latest_nav_date) }} ·
                  {{ row.points }} pts</span
                >
                <span v-else class="sec-sub">none</span>
              </span>
            </div>
          </div>
          <div v-if="g.remaining || g.grown" class="group-foot">
            <button v-if="g.remaining" type="button" class="show-more" @click="showMore(g.type)">
              <i class="pi pi-chevron-down" />
              Show {{ Math.min(50, g.remaining) }} more ({{ g.remaining }} remaining)
            </button>
            <button v-if="g.grown" type="button" class="show-more" @click="showLess(g.type)">
              <i class="pi pi-chevron-up" />
              Show less
            </button>
          </div>
        </section>
      </div>
    </template>
    <p v-else class="hint">No securities tracked yet — import a statement first.</p>
  </article>
</template>

<style scoped>
/* Self-contained card chrome — the parent view's scoped styles don't reach in. */
.nav-panel {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-4);
  background: var(--fm-surface);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
  padding: var(--fm-space-5);
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
.hint {
  color: var(--fm-text-muted);
  font-size: 0.875rem;
  margin: 0;
}

/* Freshness spectrum */
.spectrum-headline {
  margin: 0 0 0.5rem;
  font-size: 0.875rem;
  color: var(--fm-text-muted);
}
.spectrum-headline strong {
  color: var(--fm-text);
  font-variant-numeric: tabular-nums;
}
.spectrum {
  display: flex;
  height: 0.55rem;
  border-radius: 999px;
  overflow: hidden;
  gap: 2px;
}
.seg {
  min-width: 0.35rem;
  transition: width var(--fm-dur-slow) var(--fm-ease);
}
.legend {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem 1rem;
  margin: 0.55rem 0 0;
  padding: 0;
  font-size: 0.75rem;
  color: var(--fm-text-muted);
}
.legend .dot,
.lag-chip .dot {
  display: inline-block;
  width: 0.45rem;
  height: 0.45rem;
  border-radius: 50%;
  margin-right: 0.35rem;
  vertical-align: middle;
}

/* Schedule note */
.schedule {
  display: flex;
  align-items: baseline;
  gap: 0.45rem;
  margin: 0;
  padding: 0.6rem 0.8rem;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
  background: var(--fm-surface-raised);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-sm);
}
.schedule strong {
  color: var(--fm-text);
}

/* Asset-class groups */
.groups {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-5);
}
.group-head {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  margin-bottom: 0.4rem;
}
.group-head h3 {
  margin: 0;
  font-size: 0.8125rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--fm-text-muted);
}
.group-head small {
  font-size: 0.75rem;
  color: var(--fm-text-muted);
  font-variant-numeric: tabular-nums;
}

/* Row grid: name | latest | status | history */
.rows {
  display: flex;
  flex-direction: column;
}
.row {
  display: grid;
  grid-template-columns: minmax(14rem, 2.2fr) minmax(6.5rem, 0.9fr) minmax(8rem, 1fr) minmax(
      12rem,
      1.4fr
    );
  gap: var(--fm-space-4);
  align-items: center;
  padding: 0.5rem 0;
  border-top: 1px solid var(--fm-border-subtle);
}
.row.head {
  border-top: none;
  padding: 0.25rem 0 0.4rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--fm-text-muted);
}
.cell-sec {
  min-width: 0;
}
.sec-name {
  display: block;
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--fm-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sec-sub {
  display: block;
  font-size: 0.75rem;
  color: var(--fm-text-muted);
}
.mono {
  font-family: var(--fm-font-mono, monospace);
  font-size: 0.8125rem;
  font-variant-numeric: tabular-nums;
}
.lag-chip {
  display: inline-flex;
  align-items: center;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--fm-text);
  white-space: nowrap;
}
.lag-chip .dot {
  background: var(--chip);
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fm-space-3);
  flex-wrap: wrap;
}
.search {
  position: relative;
  display: inline-flex;
  align-items: center;
}
.search i {
  position: absolute;
  left: 0.65rem;
  font-size: 0.8rem;
  color: var(--fm-text-muted);
  pointer-events: none;
}
.search :deep(input) {
  padding-left: 2rem;
  min-width: 16rem;
}
.group-foot {
  display: flex;
  gap: var(--fm-space-4);
}
.show-more {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  margin-top: 0.35rem;
  padding: 0.3rem 0.1rem;
  border: none;
  background: none;
  font: inherit;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--p-primary-color);
  cursor: pointer;
}
.show-more i {
  font-size: 0.7rem;
}
.show-more:hover {
  text-decoration: underline;
}
.type-group {
  display: flex;
  flex-direction: column;
}

@media (max-width: 720px) {
  .row {
    grid-template-columns: 1fr auto;
  }
  .row.head,
  .row [role='cell']:nth-child(4) {
    display: none;
  }
}
</style>
