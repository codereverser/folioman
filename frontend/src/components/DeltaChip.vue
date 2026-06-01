<script setup lang="ts">
import { computed } from 'vue'
import { formatInr, formatPercent, toNumber, trendGlyph } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    /** Absolute change in rupees (optional). */
    amount?: number | string | null
    /** Percent change (optional). */
    percent?: number | string | null
    /** Drives colour/glyph when amount/percent could be ambiguous. */
    value?: number | string | null
    size?: 'sm' | 'md'
  }>(),
  { size: 'md' },
)

const basis = computed(() => toNumber(props.value ?? props.amount ?? props.percent))
const dir = computed<'gain' | 'loss' | 'flat'>(() =>
  basis.value > 0 ? 'gain' : basis.value < 0 ? 'loss' : 'flat',
)
const glyph = computed(() => trendGlyph(basis.value))
</script>

<template>
  <span class="delta-chip" :class="[dir, size]">
    <span class="glyph" aria-hidden="true">{{ glyph }}</span>
    <span v-if="amount !== undefined && amount !== null" class="amount">{{ formatInr(amount) }}</span>
    <span v-if="percent !== undefined && percent !== null" class="percent">{{ formatPercent(percent) }}</span>
  </span>
</template>

<style scoped>
.delta-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.35em;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  border-radius: var(--fm-radius-pill);
  white-space: nowrap;
}

.delta-chip.md {
  font-size: 0.875rem;
  padding: 0.15rem 0.5rem;
}

.delta-chip.sm {
  font-size: 0.75rem;
  padding: 0.05rem 0.4rem;
}

.delta-chip.gain {
  color: var(--fm-gain);
  background: var(--fm-gain-bg);
}

.delta-chip.loss {
  color: var(--fm-loss);
  background: var(--fm-loss-bg);
}

.delta-chip.flat {
  color: var(--fm-text-subtle);
  background: transparent;
}

.glyph {
  font-size: 0.8em;
}
</style>
