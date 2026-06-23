<script setup lang="ts">
import { computed } from 'vue'
import { integrityMeta, type IntegritySeverity, type IntegrityStatus } from '@/integrity/status'

const props = withDefaults(
  defineProps<{
    status: IntegrityStatus
    size?: 'sm' | 'md' | 'lg'
    /** Hide the text label, show glyph only (still keeps an aria-label). */
    iconOnly?: boolean
    /** Override the status's default label/severity — e.g. an incomplete-history
     * row carries `snapshot_only` but should read as "Incomplete history". */
    label?: string
    severity?: IntegritySeverity
  }>(),
  { size: 'md', iconOnly: false },
)

const base = computed(() => integrityMeta(props.status))
const meta = computed(() => ({
  ...base.value,
  label: props.label ?? base.value.label,
  severity: props.severity ?? base.value.severity,
}))
</script>

<template>
  <span
    class="integrity-badge"
    :class="[meta.severity, size]"
    :title="meta.tooltip"
    :aria-label="`${meta.label}. ${meta.tooltip}`"
    role="status"
  >
    <i :class="meta.icon" aria-hidden="true" />
    <span v-if="!iconOnly" class="label">{{ meta.label }}</span>
  </span>
</template>

<style scoped>
.integrity-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.4em;
  font-weight: 600;
  border-radius: var(--fm-radius-pill);
  border: 1px solid transparent;
  white-space: nowrap;
}

.integrity-badge.sm {
  font-size: 0.6875rem;
  padding: 0.05rem 0.45rem;
}
.integrity-badge.md {
  font-size: 0.8125rem;
  padding: 0.2rem 0.6rem;
}
.integrity-badge.lg {
  font-size: 0.9375rem;
  padding: 0.35rem 0.8rem;
}

.integrity-badge.verified {
  color: var(--fm-verified);
  background: var(--fm-verified-bg);
  border-color: color-mix(in srgb, var(--fm-verified), transparent 70%);
}
.integrity-badge.warn {
  color: var(--fm-warn);
  background: var(--fm-warn-bg);
  border-color: color-mix(in srgb, var(--fm-warn), transparent 70%);
}
.integrity-badge.critical {
  color: var(--fm-critical);
  background: var(--fm-critical-bg);
  border-color: color-mix(in srgb, var(--fm-critical), transparent 70%);
}
.integrity-badge.neutral {
  color: var(--fm-text-subtle);
  background: var(--fm-surface-raised);
  border-color: var(--fm-border-subtle);
}
</style>
