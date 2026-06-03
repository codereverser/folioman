import { createRouter, createWebHistory } from 'vue-router'
import type { RouteMeta, RouteRecordRaw } from 'vue-router'
import { useUiStore } from '@/stores/ui'
import DashboardView from '@/views/DashboardView.vue'
import FamilyView from '@/views/FamilyView.vue'
import SchemeDetailView from '@/views/SchemeDetailView.vue'

declare module 'vue-router' {
  interface RouteMeta {
    /** Import/edit routes that aren't usable on a phone — guarded to a notice. */
    desktopOnly?: boolean
  }
}

/** Edit/import routes redirect to a "desktop only" notice when on a phone. */
export function blockedOnMobile(meta: RouteMeta, isMobile: boolean): boolean {
  return isMobile && meta.desktopOnly === true
}

// Scope lives in the URL: `/investors/:investorId/...` for a single investor,
// `/families/:familyId` for a family aggregate. The route prefix is what makes
// the active scope unambiguous; the ui store is kept in sync by the guard below.
const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'home',
    // Land on the last-used scope (restored from localStorage), else the roster.
    redirect: () => {
      const ui = useUiStore()
      if (ui.selectedInvestorId !== null) {
        return { name: 'dashboard', params: { investorId: ui.selectedInvestorId } }
      }
      if (ui.selectedFamilyId !== null) {
        return { name: 'family', params: { familyId: ui.selectedFamilyId } }
      }
      return { name: 'investors' }
    },
  },
  {
    path: '/investors',
    name: 'investors',
    component: () => import('@/views/InvestorsView.vue'),
  },
  {
    path: '/investors/:investorId',
    redirect: (to) => ({ name: 'dashboard', params: { investorId: to.params.investorId } }),
  },
  {
    path: '/investors/:investorId/dashboard',
    name: 'dashboard',
    component: DashboardView,
  },
  {
    path: '/investors/:investorId/schemes/:securityId',
    name: 'scheme-detail',
    component: SchemeDetailView,
  },
  {
    path: '/investors/:investorId/integrity',
    name: 'integrity',
    component: () => import('@/views/IntegrityView.vue'),
  },
  {
    // Single CAS import — advisor-level: the statement identifies its own
    // investor by PAN (the server auto-detects MF CAS vs NSDL/CDSL eCAS and
    // resolves or creates the investor). Not scoped to a pre-selected investor.
    path: '/import',
    name: 'import',
    component: () => import('@/views/ImportView.vue'),
    meta: { desktopOnly: true },
  },
  {
    path: '/families/:familyId',
    name: 'family',
    component: FamilyView,
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/views/SettingsView.vue'),
  },
  {
    path: '/desktop-only',
    name: 'desktop-only',
    component: () => import('@/views/DesktopOnlyView.vue'),
  },
  // Unknown paths fall back to the scope resolver.
  { path: '/:pathMatch(.*)*', redirect: { name: 'home' } },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Keep the ui store's active scope in sync with the URL, so a reload on a scoped
// route (or a deep link) restores the selection that drives the switcher and nav.
router.beforeEach((to) => {
  const ui = useUiStore()
  const investorId = to.params.investorId
  const familyId = to.params.familyId
  if (typeof investorId === 'string') {
    ui.selectInvestor(Number(investorId))
  } else if (typeof familyId === 'string') {
    ui.selectFamily(Number(familyId))
  }
  // Import/edit screens are desktop-only; on a phone, divert to the notice.
  if (blockedOnMobile(to.meta, ui.isMobile)) {
    return { name: 'desktop-only', query: { from: to.fullPath } }
  }
  return true
})
