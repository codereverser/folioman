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
    links.push({
      label: 'Integrity',
      icon: 'pi pi-verified',
      to: { name: 'integrity', params: { investorId } },
    })
    links.push({
      label: 'Tax',
      icon: 'pi pi-file-edit',
      to: { name: 'tax-export', params: { investorId } },
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
      <div class="route-frame">
        <RouterView v-slot="{ Component }">
          <Suspense v-if="Component">
            <component :is="Component" />
            <template #fallback>
              <section class="route-skeleton" aria-hidden="true">
                <div class="skeleton-head">
                  <span />
                  <span />
                </div>
                <div class="skeleton-grid">
                  <span class="skeleton-card span-6 hero" />
                  <span class="skeleton-card span-3" />
                  <span class="skeleton-card span-3" />
                  <span class="skeleton-card span-4 chart" />
                  <span class="skeleton-card span-8 chart" />
                </div>
              </section>
            </template>
          </Suspense>
          <section v-else class="route-skeleton" aria-hidden="true">
            <div class="skeleton-head">
              <span />
              <span />
            </div>
            <div class="skeleton-grid">
              <span class="skeleton-card span-6 hero" />
              <span class="skeleton-card span-3" />
              <span class="skeleton-card span-3" />
              <span class="skeleton-card span-4 chart" />
              <span class="skeleton-card span-8 chart" />
            </div>
          </section>
        </RouterView>
      </div>
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
  /* Don't let the scope switcher's min-content set a floor on the grid column —
     without this the single-column mobile shell can't shrink to the viewport and
     the page scrolls sideways. */
  min-width: 0;
}

/* Flex child holding the Select — must also opt out of the auto min-width floor
   so the control can shrink and ellipsize its label. */
.switcher {
  min-width: 0;
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
  /* Let the 1fr grid track shrink below the content's min-content width — without
     this the charts/tables inside set a floor and the whole page scrolls sideways
     on a phone. The rest of the min-width:0 chain lives in each view. */
  min-width: 0;
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

.route-frame {
  flex: 1 0 auto;
  min-width: 0;
}

.route-skeleton {
  padding: var(--fm-space-6);
  max-width: var(--fm-content-max);
  margin: 0 auto;
  width: 100%;
}

.skeleton-head {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-2);
  margin-bottom: var(--fm-space-5);
}

.skeleton-head span,
.skeleton-card {
  display: block;
  border-radius: var(--fm-radius-sm);
  background:
    linear-gradient(90deg, transparent 0, color-mix(in srgb, var(--fm-border-subtle) 32%, transparent) 50%, transparent 100%),
    var(--fm-surface-raised);
}

.skeleton-head span:first-child {
  width: 10rem;
  height: 1.8rem;
}

.skeleton-head span:last-child {
  width: 13rem;
  height: 1rem;
}

.skeleton-grid {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: var(--fm-space-5);
}

.skeleton-card {
  min-height: 9rem;
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-xl);
}

.skeleton-card.hero {
  min-height: 10.5rem;
}

.skeleton-card.chart {
  min-height: 20.5rem;
}

.skeleton-card.span-3 {
  grid-column: span 3;
}

.skeleton-card.span-4 {
  grid-column: span 4;
}

.skeleton-card.span-6 {
  grid-column: span 6;
}

.skeleton-card.span-8 {
  grid-column: span 8;
}

.app-footer {
  margin-top: auto;
  padding: 1rem;
}

/* Mobile: nav collapses to a horizontal top bar above the content. Use a media
   query so the first paint has the mobile geometry before the JS viewport store
   starts tracking resize events. */
@media (max-width: 767px) {
  .app-shell {
    grid-template-columns: 1fr;
  }

  .app-nav {
    border-right: none;
    border-bottom: 1px solid var(--fm-border-subtle);
    min-height: 11.25rem;
  }

  nav {
    flex-direction: row;
    flex-wrap: nowrap;
    overflow-x: auto;
    gap: var(--fm-space-2);
  }

  /* Comfortable touch targets on phones (≥44px per iOS HIG). */
  .nav-link {
    min-height: 44px;
    white-space: nowrap;
  }

  .switcher :deep(.scope-switcher) {
    width: 100%;
  }

  .route-skeleton {
    padding: var(--fm-space-4);
  }

  .skeleton-grid {
    gap: var(--fm-space-4);
  }

  .skeleton-card.span-3,
  .skeleton-card.span-4,
  .skeleton-card.span-6,
  .skeleton-card.span-8 {
    grid-column: span 12;
  }
}
</style>
