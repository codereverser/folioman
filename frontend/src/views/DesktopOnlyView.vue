<script setup lang="ts">
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import { useUiStore } from '@/stores/ui'

const router = useRouter()
const ui = useUiStore()

function goBack(): void {
  if (ui.selectedInvestorId !== null) {
    void router.push({ name: 'dashboard', params: { investorId: ui.selectedInvestorId } })
  } else {
    void router.push({ name: 'investors' })
  }
}
</script>

<template>
  <section class="desktop-only">
    <i class="pi pi-desktop" aria-hidden="true" />
    <h1>Open on desktop to import or edit</h1>
    <p class="muted">
      Importing statements and editing data needs the full desktop layout. On your
      phone you can view dashboards, holdings and integrity — switch to Folioman on
      a computer to make changes.
    </p>
    <Button label="Back to dashboard" icon="pi pi-arrow-left" @click="goBack" />
  </section>
</template>

<style scoped>
.desktop-only {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--fm-space-3);
  padding: var(--fm-space-12) var(--fm-space-5);
  max-width: 32rem;
  margin: 0 auto;
}
.desktop-only .pi {
  font-size: 2.5rem;
  color: var(--fm-text-subtle);
}
h1 {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 600;
}
.muted {
  margin: 0;
  color: var(--fm-text-muted);
}
</style>
