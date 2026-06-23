<script setup lang="ts">
// Shown when the service worker has precached a newer build (see
// registerServiceWorker.ts). The user reloads when convenient — we never swap the
// bundle mid-action. A floating pill so it works in any layout (shell or bare).
import { ref } from 'vue'
import Button from 'primevue/button'
import { applyUpdate, updateAvailable } from '@/pwa/updateState'

const reloading = ref(false)

async function reload(): Promise<void> {
  reloading.value = true
  await applyUpdate() // activates the waiting worker, then reloads the page
}
</script>

<template>
  <Transition name="fm-update">
    <div v-if="updateAvailable" class="update-banner" role="status">
      <i class="pi pi-sync" aria-hidden="true" />
      <span class="update-text">A new version of Folioman is available.</span>
      <Button
        size="small"
        label="Reload"
        icon="pi pi-refresh"
        :loading="reloading"
        @click="reload"
      />
    </div>
  </Transition>
</template>

<style scoped>
.update-banner {
  position: fixed;
  z-index: 50;
  left: 50%;
  bottom: 1.25rem;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: var(--fm-space-3);
  max-width: min(28rem, calc(100vw - 2rem));
  padding: var(--fm-space-3) var(--fm-space-4);
  background: var(--fm-surface);
  color: var(--fm-text);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-pill);
  box-shadow: var(--fm-shadow-overlay, 0 8px 24px rgba(0, 0, 0, 0.18));
  font-size: 0.8125rem;
  font-weight: 500;
}

.update-banner .pi-sync {
  color: var(--p-primary-color);
}

.update-text {
  flex: 1;
}

/* Clear the fixed mobile bottom tab bar. */
@media (max-width: 767px) {
  .update-banner {
    bottom: 4.5rem;
  }
}

.fm-update-enter-active,
.fm-update-leave-active {
  transition:
    opacity var(--fm-dur, 0.18s) var(--fm-ease, ease),
    transform var(--fm-dur, 0.18s) var(--fm-ease, ease);
}
.fm-update-enter-from,
.fm-update-leave-to {
  opacity: 0;
  transform: translate(-50%, 0.5rem);
}
</style>
