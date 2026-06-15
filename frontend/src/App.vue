<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute, type RouteLocationRaw } from 'vue-router'
import Toast from 'primevue/toast'
import ConfirmDialog from 'primevue/confirmdialog'
import ProgressBar from 'primevue/progressbar'
import { useToast } from 'primevue/usetoast'
import ScopeSwitcher from '@/components/ScopeSwitcher.vue'
import ThemeToggle from '@/components/ThemeToggle.vue'
import PwaInstallPrompt from '@/components/PwaInstallPrompt.vue'
import DemoBanner from '@/components/DemoBanner.vue'
import { useUiStore } from '@/stores/ui'
import { useRosterStore } from '@/stores/roster'
import { useIntegrityStore } from '@/stores/integrity'
import { useMetaStore } from '@/stores/meta'

interface NavLink {
  label: string
  icon: string
  to: RouteLocationRaw
  badge?: number
}

const ui = useUiStore()
const roster = useRosterStore()
const integrity = useIntegrityStore()
const meta = useMetaStore()
const toast = useToast()
const route = useRoute()

// Auth/setup screens render full-screen, outside the app shell (no sidebar, no
// scope switcher) — they provide their own centered layout. Flagged per-route
// with `meta.bare`.
const bare = computed(() => route.meta.bare === true)

// Selected investor's items-needing-attention (mismatches) — drives the nav badge.
const attentionCount = computed(() =>
  ui.selectedInvestorId !== null ? integrity.rollupFor(ui.selectedInvestorId).needsAttention : 0,
)
watch(
  () => ui.selectedInvestorId,
  (id) => {
    if (id !== null) void integrity.load(id)
  },
  { immediate: true },
)

// Nav matches the page list; scoped links appear once a scope is selected.
const navLinks = computed<NavLink[]>(() => {
  // Import is always available (the primary way to onboard an investor); the hub
  // routes to each source — CAS/eCAS PDF, stock tradebook, …
  const links: NavLink[] = [
    { label: 'Investors', icon: 'pi pi-users', to: { name: 'investors' } },
    { label: 'Import', icon: 'pi pi-download', to: { name: 'import' } },
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
      badge: attentionCount.value || undefined,
    })
    links.push({
      label: 'Capital Gains',
      icon: 'pi pi-file-edit',
      to: { name: 'capital-gains', params: { investorId } },
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

// Mobile bottom tab bar: primary destinations only. Import is desktop-only; any
// other link (e.g. nothing extra today) is reachable via the scope switcher.
const MOBILE_TABS = new Set([
  'Investors',
  'Dashboard',
  'Integrity',
  'Capital Gains',
  'Family',
  'Settings',
])
const mobileTabs = computed<NavLink[]>(() => navLinks.value.filter((l) => MOBILE_TABS.has(l.label)))

// In the collapsed icon rail, the label lives in a hover tooltip; an empty string
// renders no tooltip (so the full sidebar and mobile rows stay tooltip-free).
function railTooltip(label: string): string {
  return ui.sidebarCollapsed && !ui.isMobile ? label : ''
}

// Drain the ui store's toast queue into PrimeVue's Toast service.
watch(
  () => ui.toasts.length,
  (len) => {
    if (len === 0) return
    for (const t of ui.drainToasts()) {
      toast.add({
        severity: t.severity,
        summary: t.summary,
        detail: t.detail,
        life: t.life ?? 4000,
      })
    }
  },
)

let stopViewport: () => void = () => {}
let stopTheme: () => void = () => {}
onMounted(() => {
  stopViewport = ui.startViewportTracking()
  stopTheme = ui.startThemeTracking()
  void roster.ensureLoaded()
  void meta.ensureLoaded()
})
onBeforeUnmount(() => {
  stopViewport()
  stopTheme()
})
</script>

<template>
  <!-- Full-screen auth/setup pages: no shell chrome, just the page + global overlays. -->
  <template v-if="bare">
    <RouterView />
    <Toast />
    <ConfirmDialog />
  </template>

  <div
    v-else
    class="app-shell"
    :class="{ 'is-mobile': ui.isMobile, 'is-collapsed': ui.sidebarCollapsed && !ui.isMobile }"
  >
    <aside class="app-nav">
      <div class="brand">
        <img src="/logo.svg" alt="" width="28" height="28" />
        <span class="brand-name">Folioman</span>
        <button
          v-tooltip.right="railTooltip('Expand')"
          type="button"
          class="collapse-toggle"
          :aria-label="ui.sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'"
          :aria-pressed="ui.sidebarCollapsed"
          @click="ui.toggleSidebar()"
        >
          <i
            :class="ui.sidebarCollapsed ? 'pi pi-angle-double-right' : 'pi pi-angle-double-left'"
          />
        </button>
      </div>
      <nav class="side-nav">
        <RouterLink
          v-for="link in navLinks"
          :key="link.label"
          v-tooltip.right="railTooltip(link.label)"
          :to="link.to"
          class="nav-link"
          active-class="is-active"
        >
          <i :class="link.icon" />
          <span class="nav-label">{{ link.label }}</span>
          <span v-if="link.badge" class="nav-badge" :title="`${link.badge} need attention`">{{
            link.badge
          }}</span>
        </RouterLink>
      </nav>
    </aside>

    <main class="app-main">
      <DemoBanner />
      <ProgressBar v-if="ui.loading" mode="indeterminate" class="loading-bar" />
      <header class="context-bar">
        <div class="ctx-scope">
          <ScopeSwitcher />
        </div>
        <div class="ctx-right">
          <span class="privacy-indicator" title="Your data stays on this device.">
            <i class="pi pi-lock" />
            <span>Local</span>
          </span>
          <ThemeToggle />
        </div>
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

    <!-- Mobile primary navigation: a fixed bottom tab bar (hidden on desktop). -->
    <nav class="bottom-tabs" aria-label="Primary">
      <RouterLink
        v-for="tab in mobileTabs"
        :key="tab.label"
        :to="tab.to"
        class="tab"
        active-class="is-active"
      >
        <span class="tab-icon">
          <i :class="tab.icon" />
          <span v-if="tab.badge" class="tab-badge" :aria-label="`${tab.badge} need attention`">{{
            tab.badge
          }}</span>
        </span>
        <span class="tab-label">{{ tab.label }}</span>
      </RouterLink>
    </nav>

    <Toast />
    <ConfirmDialog />
  </div>
</template>

<style scoped>
.app-shell {
  display: grid;
  grid-template-columns: 16rem 1fr;
  min-height: 100vh;
  transition: grid-template-columns var(--fm-dur, 0.18s) var(--fm-ease, ease);
}

/* Collapsed: the sidebar becomes a 4.5rem icon rail (DESIGN-SYSTEM §5). */
.app-shell.is-collapsed {
  grid-template-columns: 4.5rem 1fr;
}

.app-nav {
  background: var(--fm-surface);
  border-right: 1px solid var(--fm-border-subtle);
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  /* Keep the nav column from setting a min-content floor on the grid track —
     without this the single-column mobile shell can't shrink to the viewport and
     the page scrolls sideways. */
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
  position: relative;
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

/* Selected route reads as a tinted teal pill with a leading accent rail — the
   pale highlight token alone didn't separate from the white sidebar. */
.nav-link.is-active {
  background: color-mix(in oklab, var(--p-primary-color) 14%, var(--fm-surface));
  color: var(--p-primary-color);
  font-weight: 600;
}
.nav-link.is-active:hover {
  background: color-mix(in oklab, var(--p-primary-color) 20%, var(--fm-surface));
}
.nav-link.is-active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  height: 1.25rem;
  width: 3px;
  transform: translateY(-50%);
  border-radius: 0 var(--fm-radius-pill) var(--fm-radius-pill) 0;
  background: var(--p-primary-color);
}
.nav-label {
  flex: 1;
}
/* Items-needing-attention count on the Integrity nav item. */
.nav-badge {
  min-width: 1.1rem;
  padding: 0 0.35rem;
  border-radius: var(--fm-radius-pill);
  background: var(--fm-critical);
  color: #fff;
  font-size: 0.6875rem;
  font-weight: 700;
  line-height: 1.1rem;
  text-align: center;
}

/* Collapse / expand control — a compact icon button on the brand line. */
.collapse-toggle {
  margin-left: auto; /* push to the far end of the brand line */
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.9rem;
  height: 1.9rem;
  border: none;
  background: transparent;
  border-radius: var(--fm-radius-sm);
  color: var(--fm-text-muted);
  font-size: 0.9rem;
  cursor: pointer;
  transition:
    background var(--fm-dur-fast) var(--fm-ease),
    color var(--fm-dur-fast) var(--fm-ease);
}
.collapse-toggle:hover {
  background: var(--fm-surface-raised);
  color: var(--fm-text);
}

/* ---- collapsed icon rail ---- */
.is-collapsed .app-nav {
  padding: 1rem 0.5rem;
  align-items: stretch;
}
/* Stack logo + toggle so the control stays at the top of the rail. */
.is-collapsed .brand {
  flex-direction: column;
  gap: 0.5rem;
}
.is-collapsed .collapse-toggle {
  margin-left: 0;
}
.is-collapsed .brand-name,
.is-collapsed .nav-label {
  display: none;
}
.is-collapsed .nav-link {
  position: relative;
  justify-content: center;
  gap: 0;
  padding: 0.6rem 0;
}
/* With the label hidden, float the attention badge over the icon. */
.is-collapsed .nav-badge {
  position: absolute;
  top: 0.1rem;
  right: 0.55rem;
}

/* Bottom tab bar — mobile only (shown via the media query below). */
.bottom-tabs {
  display: none;
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

/* The scope switcher lives in the top bar (DESIGN-SYSTEM §5): always visible,
   independent of the sidebar's collapsed state. Capped so a long name doesn't
   stretch the control across the bar. */
.ctx-scope {
  min-width: 0;
  flex: 0 1 22rem;
}
.ctx-right {
  display: flex;
  align-items: center;
  gap: var(--fm-space-3);
  flex-shrink: 0;
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
    linear-gradient(
      90deg,
      transparent 0,
      color-mix(in srgb, var(--fm-border-subtle) 32%, transparent) 50%,
      transparent 100%
    ),
    var(--fm-surface-raised);
  /* Shared branded shimmer (keyframe in style.css). */
  background-size:
    200% 100%,
    auto;
  animation: fm-shimmer 1.4s ease-in-out infinite;
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
    flex-direction: row;
    align-items: center;
    gap: var(--fm-space-3);
    border-right: none;
    border-bottom: 1px solid var(--fm-border-subtle);
    padding: var(--fm-space-3) var(--fm-space-4);
  }

  /* The sidebar link list moves to the bottom tab bar on mobile; the
     collapse control is desktop/tablet-only. */
  .side-nav,
  .collapse-toggle {
    display: none;
  }

  /* Top bar: let the scope switcher take the room and shrink/ellipsize. */
  .context-bar {
    padding: var(--fm-space-3) var(--fm-space-4);
  }
  .ctx-scope {
    flex: 1 1 auto;
  }

  /* Bottom tab bar: fixed primary navigation, comfortable touch targets. */
  .bottom-tabs {
    display: flex;
    flex-direction: row; /* override the base `nav { flex-direction: column }` */
    position: fixed;
    inset: auto 0 0 0;
    z-index: 20;
    background: var(--fm-surface);
    border-top: 1px solid var(--fm-border-subtle);
    padding-bottom: env(safe-area-inset-bottom, 0);
  }
  .tab {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.15rem;
    min-height: 48px;
    padding: 0.4rem 0.25rem;
    text-decoration: none;
    color: var(--fm-text-muted);
    font-size: 0.625rem;
  }
  .tab.is-active {
    color: var(--p-primary-color);
  }
  .tab-icon {
    position: relative;
    font-size: 1.15rem;
    line-height: 1;
  }
  .tab-label {
    max-width: 100%;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tab-badge {
    position: absolute;
    top: -0.4rem;
    left: 0.65rem;
    min-width: 0.95rem;
    height: 0.95rem;
    padding: 0 0.2rem;
    border-radius: var(--fm-radius-pill);
    background: var(--fm-critical);
    color: #fff;
    font-size: 0.5625rem;
    font-weight: 700;
    line-height: 0.95rem;
    text-align: center;
  }

  /* Keep content clear of the fixed bottom bar. */
  .app-main {
    padding-bottom: 3.75rem;
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
