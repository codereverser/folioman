<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import type { RouteLocationRaw } from 'vue-router'
import Toast from 'primevue/toast'
import ConfirmDialog from 'primevue/confirmdialog'
import ProgressBar from 'primevue/progressbar'
import { useToast } from 'primevue/usetoast'
import ScopeSwitcher from '@/components/ScopeSwitcher.vue'
import ThemeToggle from '@/components/ThemeToggle.vue'
import PwaInstallPrompt from '@/components/PwaInstallPrompt.vue'
import { useUiStore } from '@/stores/ui'
import { useRosterStore } from '@/stores/roster'

interface NavLink {
  label: string
  icon: string
  to: RouteLocationRaw
}

const ui = useUiStore()
const roster = useRosterStore()
const toast = useToast()

// Nav matches the page list; scoped links appear once a scope is selected.
const navLinks = computed<NavLink[]>(() => {
  // Import is advisor-level (the CAS identifies its own investor by PAN), so it's
  // always available — it's the primary way to onboard an investor.
  const links: NavLink[] = [
    { label: 'Investors', icon: 'pi pi-users', to: { name: 'investors' } },
    { label: 'Import CAS', icon: 'pi pi-file-pdf', to: { name: 'import' } },
  ]
  if (ui.selectedInvestorId !== null) {
    const investorId = ui.selectedInvestorId
    links.push({
      label: 'Dashboard',
      icon: 'pi pi-chart-line',
      to: { name: 'dashboard', params: { investorId } },
    })
  }
  if (ui.selectedFamilyId !== null) {
    links.push({
      label: 'Family',
      icon: 'pi pi-sitemap',
      to: { name: 'family', params: { familyId: ui.selectedFamilyId } },
    })
  }
  links.push({ label: 'Settings', icon: 'pi pi-cog', to: { name: 'settings' } })
  return links
})

// Drain the ui store's toast queue into PrimeVue's Toast service.
watch(
  () => ui.toasts.length,
  (len) => {
    if (len === 0) return
    for (const t of ui.drainToasts()) {
      toast.add({ severity: t.severity, summary: t.summary, detail: t.detail, life: t.life ?? 4000 })
    }
  },
)

let stopViewport: () => void = () => {}
let stopTheme: () => void = () => {}
onMounted(() => {
  stopViewport = ui.startViewportTracking()
  stopTheme = ui.startThemeTracking()
  void roster.ensureLoaded()
})
onBeforeUnmount(() => {
  stopViewport()
  stopTheme()
})
</script>

<template>
  <div class="app-shell" :class="{ 'is-mobile': ui.isMobile }">
    <aside class="app-nav">
      <div class="brand">
        <img src="/logo.svg" alt="" width="28" height="28" />
        <span class="brand-name">Folioman</span>
      </div>
      <div class="switcher">
        <ScopeSwitcher />
      </div>
      <nav>
        <RouterLink
          v-for="link in navLinks"
          :key="link.label"
          :to="link.to"
          class="nav-link"
          active-class="is-active"
        >
          <i :class="link.icon" />
          <span>{{ link.label }}</span>
        </RouterLink>
      </nav>
    </aside>

    <main class="app-main">
      <ProgressBar v-if="ui.loading" mode="indeterminate" class="loading-bar" />
      <header class="context-bar">
        <span class="privacy-indicator" title="Your data stays on this device.">
          <i class="pi pi-lock" />
          <span>Local</span>
        </span>
        <ThemeToggle />
      </header>
      <RouterView />
      <footer class="app-footer">
        <PwaInstallPrompt />
      </footer>
    </main>

    <Toast />
    <ConfirmDialog />
  </div>
</template>

<style scoped>
.app-shell {
  display: grid;
  grid-template-columns: 16rem 1fr;
  min-height: 100vh;
}

.app-nav {
  background: var(--fm-surface);
  border-right: 1px solid var(--fm-border-subtle);
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.brand {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 700;
  font-size: 1.1rem;
}

nav {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.nav-link {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.5rem 0.6rem;
  border-radius: var(--fm-radius-sm);
  text-decoration: none;
  color: var(--fm-text);
  transition: background var(--fm-dur-fast) var(--fm-ease);
}

.nav-link:hover {
  background: var(--fm-surface-raised);
}

.nav-link.is-active {
  background: var(--p-highlight-background);
  color: var(--p-primary-color);
  font-weight: 600;
}

.app-main {
  position: relative;
  display: flex;
  flex-direction: column;
  background: var(--fm-ground);
}

.context-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fm-space-3);
  padding: var(--fm-space-3) var(--fm-space-6);
  border-bottom: 1px solid var(--fm-border-subtle);
}

.privacy-indicator {
  display: inline-flex;
  align-items: center;
  gap: var(--fm-space-2);
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--fm-verified);
}

.loading-bar {
  height: 3px;
}

.app-footer {
  margin-top: auto;
  padding: 1rem;
}

/* Mobile: nav collapses to a horizontal top bar above the content. */
.app-shell.is-mobile {
  grid-template-columns: 1fr;
}

.app-shell.is-mobile .app-nav {
  border-right: none;
  border-bottom: 1px solid var(--fm-border-subtle);
}

.app-shell.is-mobile nav {
  flex-direction: row;
  flex-wrap: nowrap;
  overflow-x: auto;
  gap: var(--fm-space-2);
}

/* Comfortable touch targets on phones (≥44px per iOS HIG). */
.app-shell.is-mobile .nav-link {
  min-height: 44px;
  white-space: nowrap;
}

.app-shell.is-mobile .switcher :deep(.scope-switcher) {
  width: 100%;
}
</style>
