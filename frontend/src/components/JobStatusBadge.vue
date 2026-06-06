<script setup lang="ts">
// A small status pill for import-job / valuation statuses, shared by the Settings
// panel and the Import screen so the visual stays consistent.
import { jobStatusLabel, jobStatusTone } from '@/utils/jobs'

defineProps<{ status: string }>()
</script>

<template>
  <span class="badge" :class="jobStatusTone(status)">{{ jobStatusLabel(status) }}</span>
</template>

<style scoped>
.badge {
  flex: 0 0 auto;
  font-size: 0.6875rem;
  font-weight: 600;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  white-space: nowrap;
  border: 1px solid transparent;
}
.badge.ok {
  color: var(--p-green-600, #16a34a);
  background: color-mix(in srgb, var(--p-green-500, #22c55e) 14%, transparent);
}
.badge.bad {
  color: var(--p-red-600, #dc2626);
  background: color-mix(in srgb, var(--p-red-500, #ef4444) 14%, transparent);
}
.badge.warn {
  color: var(--p-amber-600, #d97706);
  background: color-mix(in srgb, var(--p-amber-500, #f59e0b) 16%, transparent);
}
.badge.busy {
  color: var(--fm-text-muted);
  background: var(--fm-surface-raised);
  border-color: var(--fm-border-subtle);
}
</style>
