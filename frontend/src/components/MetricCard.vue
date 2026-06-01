<script setup lang="ts">
import { computed, toRef } from 'vue'
import { useCountUp } from '@/composables/useCountUp'
import { formatInr, formatPercent, toNumber } from '@/utils/format'
import DeltaChip from '@/components/DeltaChip.vue'

const props = withDefaults(
  defineProps<{
    label: string
    /** Numeric value; formatted per `format`. */
    value: number | string | null
    format?: 'inr' | 'percent' | 'raw'
    /** Optional pre-formatted string that overrides `value`/`format`. */
    display?: string
    deltaAmount?: number | string | null
    deltaPercent?: number | string | null
    hero?: boolean
    /** Animate the value counting up on mount. */
    countUp?: boolean
  }>(),
  { format: 'inr', hero: false, countUp: false },
)

const numeric = computed(() => toNumber(props.value))
const counted = useCountUp(toRef(() => (props.countUp ? numeric.value : 0)))

const formatted = computed(() => {
  if (props.display) return props.display
  const n = props.countUp ? counted.value : numeric.value
  if (props.format === 'percent') return formatPercent(n, false)
  if (props.format === 'raw') return String(props.value ?? '—')
  return formatInr(n)
})
</script>

<template>
  <article class="metric-card" :class="{ hero }">
    <p class="eyebrow">{{ label }}</p>
    <p class="value">{{ formatted }}</p>
    <DeltaChip
      v-if="deltaAmount !== undefined || deltaPercent !== undefined"
      class="delta"
      :amount="deltaAmount"
      :percent="deltaPercent"
      :size="hero ? 'md' : 'sm'"
    />
    <slot />
  </article>
</template>

<style scoped>
.metric-card {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: var(--fm-space-5);
  background: var(--fm-surface);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
  box-shadow: var(--fm-shadow-sm);
  transition:
    transform var(--fm-dur) var(--fm-ease),
    box-shadow var(--fm-dur) var(--fm-ease);
}

.metric-card:hover {
  transform: translateY(-1px) scale(1.005);
  box-shadow: var(--fm-shadow-md);
}

.eyebrow {
  margin: 0;
  font-size: 0.75rem;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--fm-text-muted);
}

.value {
  margin: 0;
  font-size: 1.75rem;
  font-weight: 600;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  color: var(--fm-text);
}

.hero .value {
  font-size: 2.25rem;
}

.delta {
  margin-top: 0.1rem;
}

@media (prefers-reduced-motion: reduce) {
  .metric-card:hover {
    transform: none;
  }
}
</style>
