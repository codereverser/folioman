<script setup lang="ts">
// A slim top strip shown when the instance runs in read-only demo mode. The
// server enforces read-only (writes 403 regardless); this just tells the visitor.
import { useMetaStore } from '@/stores/meta'

const meta = useMetaStore()
</script>

<template>
  <div v-if="meta.readOnly" class="demo-banner" role="status">
    <i class="pi pi-eye" aria-hidden="true" />
    <span>This is a read-only demo instance — sign-ups, imports, and edits are disabled.</span>
  </div>
</template>

<style scoped>
.demo-banner {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--fm-space-2);
  padding: var(--fm-space-2) var(--fm-space-4);
  background: var(--fm-warn-bg);
  color: var(--fm-text);
  border-bottom: 1px solid var(--fm-warn);
  font-size: 0.8125rem;
  font-weight: 500;
  text-align: center;
}

.demo-banner .pi {
  color: var(--fm-warn);
  font-size: 0.875rem;
}
</style>
