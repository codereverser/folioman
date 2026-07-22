<script setup lang="ts">
import { computed, ref } from 'vue'
import Popover from 'primevue/popover'
import DeltaChip from '@/components/DeltaChip.vue'
import type { HoldingRow } from '@/composables/useDashboard'
import { formatInr } from '@/utils/format'

const props = defineProps<{ holdings: HoldingRow[]; investorId: number }>()

// Asset classes on the roadmap — shown as a teaser row at the end of the list,
// framed as "planned · vote", not promised. The poll is a GitHub Discussion.
const POLL_URL = 'https://github.com/codereverser/folioman/discussions/52'
const PLANNED: { label: string; icon: string }[] = [
  { label: 'US stocks', icon: 'pi pi-globe' },
  { label: 'Gold (SGB / digital)', icon: 'pi pi-star' },
  { label: 'Bonds & G-Secs', icon: 'pi pi-building-columns' },
  { label: 'Fixed deposits', icon: 'pi pi-wallet' },
  { label: 'Crypto (VDA)', icon: 'pi pi-bitcoin' },
  { label: 'Real estate', icon: 'pi pi-home' },
]
const moreOp = ref<InstanceType<typeof Popover>>()

interface ClassRow {
  type: string
  label: string
  color: string
  count: number
  value: number
  invested: number
  gain: number | null
  returnPct: number | null
  pct: number
}

// One row per asset class, largest first. Return is the class's money-weighted
// figure (Σgain / Σinvested) over the holdings whose cost basis is known.
const rows = computed<ClassRow[]>(() => {
  const grand = props.holdings.reduce((s, h) => s + h.value, 0) || 1
  const by = new Map<string, HoldingRow[]>()
  for (const h of props.holdings) {
    const arr = by.get(h.securityType)
    if (arr) arr.push(h)
    else by.set(h.securityType, [h])
  }
  return [...by.entries()]
    .map(([type, hs]) => {
      const value = hs.reduce((s, h) => s + h.value, 0)
      const withCost = hs.filter((h) => h.invested != null)
      const invested = withCost.reduce((s, h) => s + (h.invested ?? 0), 0)
      const gain = withCost.length ? withCost.reduce((s, h) => s + (h.gain ?? 0), 0) : null
      return {
        type,
        label: hs[0].assetClass,
        color: hs[0].color,
        count: hs.length,
        value,
        invested,
        gain,
        returnPct: gain != null && invested > 0 ? (gain / invested) * 100 : null,
        pct: (value / grand) * 100,
      }
    })
    .sort((a, b) => b.value - a.value)
})
</script>

<template>
  <section class="asset-summary">
    <h2 class="title">Holdings</h2>
    <p class="caption">By asset class — open one to see its securities.</p>

    <div class="rows fm-card">
      <RouterLink
        v-for="r in rows"
        :key="r.type"
        class="row"
        :to="{ name: 'asset-class', params: { investorId, assetType: r.type } }"
      >
        <span class="swatch" :style="{ background: r.color }" aria-hidden="true" />
        <span class="name">{{ r.label }}</span>
        <span class="meta">{{ r.count }} · {{ r.pct.toFixed(0) }}%</span>
        <span class="spacer" />
        <span class="value">{{ formatInr(r.value) }}</span>
        <DeltaChip
          v-if="r.returnPct !== null"
          class="ret"
          :percent="r.returnPct"
          :value="r.returnPct"
          size="sm"
        />
        <span v-else class="ret muted">—</span>
        <i class="pi pi-chevron-right chev" aria-hidden="true" />
      </RouterLink>

      <!-- Roadmap teaser: more asset classes are coming; vote on the order. -->
      <button type="button" class="row teaser" aria-haspopup="true" @click="moreOp?.toggle($event)">
        <span class="swatch dashed" aria-hidden="true"><i class="pi pi-plus" /></span>
        <span class="name">More asset classes</span>
        <span class="meta">gold · bonds · crypto · FDs…</span>
        <span class="spacer" />
        <span class="vote-hint">Vote</span>
        <i class="pi pi-chevron-right chev" aria-hidden="true" />
      </button>
    </div>

    <Popover ref="moreOp">
      <div class="more-pop">
        <p class="more-head">On the roadmap</p>
        <ul class="more-list">
          <li v-for="a in PLANNED" :key="a.label">
            <i :class="a.icon" aria-hidden="true" />{{ a.label }}
          </li>
        </ul>
        <a class="more-vote" :href="POLL_URL" target="_blank" rel="noopener noreferrer">
          <i class="pi pi-thumbs-up" aria-hidden="true" /> Vote for what's next →
        </a>
      </div>
    </Popover>
  </section>
</template>

<style scoped>
.asset-summary {
  margin-top: var(--fm-space-5);
}
.title {
  margin: 0;
  font-size: 1rem;
  font-weight: 600;
}
.caption {
  margin: 0.15rem 0 var(--fm-space-3);
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.rows {
  padding: 0;
  overflow: hidden;
}
.row {
  display: flex;
  align-items: center;
  gap: var(--fm-space-3);
  padding: var(--fm-space-4) var(--fm-space-5);
  border-top: 1px solid var(--fm-border-subtle);
  text-decoration: none;
  color: inherit;
  transition: background var(--fm-dur-fast, 0.15s) var(--fm-ease, ease);
}
.row:first-child {
  border-top: none;
}
.row:hover {
  background: var(--fm-surface-raised);
}
.row:focus-visible {
  outline: 2px solid var(--p-primary-color, var(--fm-verified));
  outline-offset: -2px;
}
.swatch {
  width: 11px;
  height: 11px;
  border-radius: 3px;
  flex: none;
}
.name {
  font-weight: 600;
  font-size: 0.9375rem;
}
.meta {
  font-size: 0.8125rem;
  color: var(--fm-text-subtle);
  font-variant-numeric: tabular-nums;
}
.spacer {
  flex: 1;
}
.value {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.ret {
  min-width: 4.5rem;
  text-align: right;
}
.muted {
  color: var(--fm-text-subtle);
}
.chev {
  color: var(--fm-text-subtle);
  font-size: 0.75rem;
}

/* Roadmap teaser row — a dimmed, non-data row that opens the planned-classes popover. */
.row.teaser {
  width: 100%;
  background: none;
  border-left: none;
  font: inherit;
  cursor: pointer;
  color: var(--fm-text-muted);
}
.row.teaser .name {
  font-weight: 500;
  color: var(--fm-text-muted);
}
.swatch.dashed {
  display: grid;
  place-items: center;
  background: none;
  border: 1px dashed var(--fm-border);
  color: var(--fm-text-subtle);
}
.swatch.dashed .pi {
  font-size: 0.5rem;
}
.vote-hint {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--p-primary-color, var(--fm-verified));
}

.more-pop {
  min-width: 14rem;
  padding: 0.25rem;
}
.more-head {
  margin: 0 0 0.5rem;
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--fm-text-muted);
}
.more-list {
  list-style: none;
  margin: 0 0 0.6rem;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
.more-list li {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.4rem 0.5rem;
  border-radius: var(--fm-radius-sm);
  color: var(--fm-text);
  font-size: 0.875rem;
}
.more-list li i {
  color: var(--fm-text-subtle);
  width: 1rem;
  text-align: center;
}
.more-vote {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  padding: 0.55rem 0.6rem;
  border-top: 1px solid var(--fm-border-subtle);
  margin-top: 0.2rem;
  color: var(--p-primary-color, var(--fm-verified));
  font-weight: 600;
  font-size: 0.875rem;
  text-decoration: none;
}
.more-vote:hover {
  opacity: 0.85;
}
</style>
