<script setup lang="ts">
import { computed } from 'vue'
import DeltaChip from '@/components/DeltaChip.vue'
import type { HoldingRow } from '@/composables/useDashboard'
import { formatInr } from '@/utils/format'

const props = defineProps<{ holdings: HoldingRow[]; investorId: number }>()

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

    <div class="rows card">
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
    </div>
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
  background: var(--fm-surface);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
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
</style>
