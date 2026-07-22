<script setup lang="ts">
import { computed } from 'vue'
import type { PeriodReturn } from '@/composables/useDashboard'

const props = defineProps<{ returns: PeriodReturn[] }>()

// Annualize only windows ≥ 1Y (annualizing a 3-month move is noise); otherwise
// show the absolute holding-period return.
const cells = computed(() =>
  props.returns.map((r) => {
    const annualized = r.days >= 365
    const pct = annualized ? r.annualized : (r.absolute ?? r.annualized)
    return { period: r.period, pct, pa: annualized, up: pct >= 0 }
  }),
)
function formatPct(v: number): string {
  return `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(2)}%`
}
</script>

<template>
  <section v-if="cells.length" class="fm-card returns-strip">
    <div class="returns-head">
      <span class="eyebrow">Returns</span>
      <span class="returns-note">money-weighted · <span class="pa-note">p.a.</span> for ≥ 1Y</span>
    </div>
    <div class="returns-grid">
      <div
        v-for="c in cells"
        :key="c.period"
        class="ret"
        :class="{ 'ret-all': c.period === 'All' }"
      >
        <span class="ret-period">{{ c.period }}</span>
        <span class="ret-val" :class="c.up ? 'up' : 'down'"
          >{{ formatPct(c.pct) }}<span v-if="c.pa" class="ret-pa"> p.a.</span></span
        >
      </div>
    </div>
  </section>
</template>

<style scoped>
.returns-strip {
  padding: var(--fm-space-4) var(--fm-space-5);
}
.returns-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--fm-space-3);
  margin-bottom: var(--fm-space-3);
}
.eyebrow {
  margin: 0;
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--fm-text-muted);
}
.returns-note {
  font-size: 0.6875rem;
  color: var(--fm-text-subtle);
}
.returns-note .pa-note {
  color: var(--fm-text-muted);
}
.returns-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(0, 1fr));
  gap: var(--fm-space-3);
}
.ret {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  align-items: flex-end;
  text-align: right;
}
.ret-all {
  border-left: 1px solid var(--fm-border-subtle);
}
.ret-period {
  font-size: 0.6875rem;
  color: var(--fm-text-subtle);
  font-variant-numeric: tabular-nums;
}
.ret-val {
  font-size: 0.9375rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.ret-val.up {
  color: var(--fm-gain);
}
.ret-val.down {
  color: var(--fm-loss);
}
.ret-pa {
  font-size: 0.625rem;
  font-weight: 500;
  color: var(--fm-text-subtle);
}
</style>
