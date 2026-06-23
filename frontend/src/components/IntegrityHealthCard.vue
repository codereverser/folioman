<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import type { RouteLocationRaw } from 'vue-router'
import Button from 'primevue/button'
import type { IntegrityRollup } from '@/integrity/status'

const props = defineProps<{
  rollup: IntegrityRollup
  reviewTo: RouteLocationRaw
}>()

// Proportion of holdings that are tax-ready — the headline trust number.
const readyPct = computed(() =>
  props.rollup.total === 0 ? 0 : Math.round((props.rollup.taxReady / props.rollup.total) * 100),
)
const allClear = computed(
  () => props.rollup.total > 0 && props.rollup.needsAttention === 0 && props.rollup.snapshot === 0,
)
</script>

<template>
  <article class="health-card">
    <header>
      <p class="eyebrow">Data integrity</p>
      <RouterLink :to="reviewTo" class="review-link">
        <Button
          label="Review"
          icon="pi pi-arrow-right"
          icon-pos="right"
          size="small"
          :severity="rollup.needsAttention > 0 ? 'danger' : 'secondary'"
          :outlined="rollup.needsAttention === 0"
        />
      </RouterLink>
    </header>

    <div class="headline">
      <span class="ready">{{ rollup.taxReady }}</span>
      <span class="of">/ {{ rollup.total }} tax-ready</span>
    </div>

    <div class="bar" role="img" :aria-label="`${readyPct}% of holdings are tax-ready`">
      <span
        class="seg verified"
        :style="{ width: rollup.total ? (rollup.verified / rollup.total) * 100 + '%' : '0' }"
      />
      <span
        class="seg snapshot"
        :style="{ width: rollup.total ? (rollup.snapshot / rollup.total) * 100 + '%' : '0' }"
      />
      <span
        class="seg mismatch"
        :style="{ width: rollup.total ? (rollup.mismatch / rollup.total) * 100 + '%' : '0' }"
      />
      <span
        class="seg ack"
        :style="{ width: rollup.total ? (rollup.acknowledged / rollup.total) * 100 + '%' : '0' }"
      />
    </div>

    <ul class="legend">
      <li class="verified"><i class="pi pi-verified" /> {{ rollup.verified }} verified</li>
      <li v-if="rollup.snapshot" class="snapshot">
        <i class="pi pi-exclamation-triangle" /> {{ rollup.snapshot }} snapshot
      </li>
      <li v-if="rollup.mismatch" class="mismatch">
        <i class="pi pi-times-circle" /> {{ rollup.mismatch }} to fix
      </li>
      <li v-if="rollup.acknowledged" class="ack">
        <i class="pi pi-minus-circle" /> {{ rollup.acknowledged }} acknowledged
      </li>
    </ul>

    <p v-if="allClear" class="all-clear">
      <i class="pi pi-shield" /> All holdings verified — Schedule 112A ready.
    </p>
  </article>
</template>

<style scoped>
.health-card {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  padding: var(--fm-space-5);
  background: var(--fm-surface);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
  box-shadow: var(--fm-shadow-sm);
}

header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.eyebrow {
  margin: 0;
  font-size: 0.75rem;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--fm-text-muted);
}

.review-link {
  text-decoration: none;
}

.headline {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  font-variant-numeric: tabular-nums;
}

.headline .ready {
  font-size: 1.75rem;
  font-weight: 600;
  color: var(--fm-verified);
}

.headline .of {
  font-size: 0.9375rem;
  color: var(--fm-text-muted);
}

.bar {
  display: flex;
  height: 8px;
  border-radius: var(--fm-radius-pill);
  overflow: hidden;
  background: var(--fm-surface-raised);
}

.seg {
  height: 100%;
  transition: width var(--fm-dur-slow) var(--fm-ease);
}
.seg.verified {
  background: var(--fm-verified);
}
.seg.snapshot {
  background: var(--fm-warn);
}
.seg.mismatch {
  background: var(--fm-critical);
}
.seg.ack {
  background: var(--fm-ack);
}

.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  list-style: none;
  margin: 0;
  padding: 0;
  font-size: 0.8125rem;
  font-weight: 500;
}
.legend li {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}
.legend .verified {
  color: var(--fm-verified);
}
.legend .snapshot {
  color: var(--fm-warn);
}
.legend .mismatch {
  color: var(--fm-critical);
}
.legend .ack {
  color: var(--fm-text-subtle);
}

.all-clear {
  margin: 0;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.8125rem;
  color: var(--fm-verified);
}
</style>
