import { createRouter, createWebHistory } from 'vue-router'
import type { RouteLocationRaw, RouteMeta, RouteRecordRaw } from 'vue-router'
import { useUiStore } from '@/stores/ui'
import { useRosterStore } from '@/stores/roster'
import { useAuthStore } from '@/stores/auth'
import { fetchSetupNeeded } from '@/api/setup'
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

/** Resolve a visit to the `/login` or `/setup` screen. Pure so it's unit-tested.
 *
 * - Signed in → neither screen applies: login returns the saved `redirect` (else
 *   home), setup returns home.
 * - Not signed in, server needs its first admin → force `/setup` (bounce login → setup).
 * - Not signed in, no setup pending → login is the place (bounce setup → login).
 *
 * Returns `true` to let the navigation through unchanged. */
export function authRouteTarget(
  toName: 'login' | 'setup',
  isAuthenticated: boolean,
  needsSetup: boolean,
  redirect: unknown,
): RouteLocationRaw | true {
  if (isAuthenticated) {
    if (toName === 'login') {
      return typeof redirect === 'string' && redirect ? redirect : { name: 'home' }
    }
    return { name: 'home' }
  }
  if (needsSetup) return toName === 'setup' ? true : { name: 'setup' }
  return toName === 'setup' ? { name: 'login' } : true
}

/** A scoped URL (`/investors/:id/...` or `/families/:id`) whose id isn't in the roster
 * is stale — the investor/family was deleted, or the DB was reset while the browser kept
 * the last scope in localStorage. Bounce such a visit to the roster (the base landing).
 * Pure so it's unit-tested; the guard clears the persisted scope alongside it. */
export function staleScopeRedirect(
  investorId: string | undefined,
  familyId: string | undefined,
  knownInvestorIds: readonly number[],
  knownFamilyIds: readonly number[],
): RouteLocationRaw | null {
  if (typeof investorId === 'string' && !knownInvestorIds.includes(Number(investorId))) {
    return { name: 'investors' }
  }
  if (typeof familyId === 'string' && !knownFamilyIds.includes(Number(familyId))) {
    return { name: 'investors' }
  }
  return null
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
    // Optional asset-class tab in the path so each is deep-linkable: no segment =
    // All (cross-asset), `/mf` = the mutual-fund breakdown. The `(mf)` constraint
    // keeps the `dashboard` route name working for plain `{investorId}` links and
    // rejects unknown asset segments. Future asset classes widen the pattern.
    path: '/investors/:investorId/dashboard/:assetTab(mf|stocks)?',
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
    path: '/investors/:investorId/capital-gains',
    name: 'capital-gains',
    component: () => import('@/views/CapitalGainsView.vue'),
  },
  {
    // Old path before the "Capital Gains" reframe — keep deep links working.
    path: '/investors/:investorId/tax',
    redirect: (to) => ({ name: 'capital-gains', params: { investorId: to.params.investorId } }),
  },
  {
    // Import hub: pick a source (CAS/eCAS PDF, stock tradebook, …), then route to
    // that flow. Intent-first, because the flows differ (CAS is PAN-resolved; a
    // tradebook is investor-chosen + column-mapped).
    path: '/import',
    name: 'import',
    component: () => import('@/views/ImportHubView.vue'),
    meta: { desktopOnly: true },
  },
  {
    // CAS/eCAS PDF — advisor-level: the statement identifies its own investor by
    // PAN (the server auto-detects MF CAS vs NSDL/CDSL eCAS and resolves or creates
    // the investor). Not scoped to a pre-selected investor.
    path: '/import/cas',
    name: 'import-cas',
    component: () => import('@/views/ImportView.vue'),
    meta: { desktopOnly: true },
  },
  {
    // Broker stock tradebook (CSV/XLSX) → canonical transaction ledger. Unlike a
    // CAS, a tradebook carries no owner identity, so the investor is chosen up front.
    path: '/import/transactions',
    name: 'import-transactions',
    component: () => import('@/views/TradebookImportView.vue'),
    meta: { desktopOnly: true },
  },
  {
    path: '/families/:familyId',
    name: 'family',
    component: FamilyView,
  },
  {
    // Optional ":tab" selects a Settings sub-tab; bare /settings is the general
    // tab. Deep-linkable so other screens can point at /settings/jobs or /settings/navs.
    path: '/settings/:tab(jobs|navs)?',
    name: 'settings',
    component: () => import('@/views/SettingsView.vue'),
  },
  {
    path: '/desktop-only',
    name: 'desktop-only',
    component: () => import('@/views/DesktopOnlyView.vue'),
  },
  {
    // Server-mode sign-in. Never reached in desktop/local mode (no request 401s),
    // so it stays out of the way there.
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    meta: { bare: true },
  },
  {
    // Server-mode first-run admin creation. The guard routes here when the server
    // has no users yet; never reached in desktop/local mode.
    path: '/setup',
    name: 'setup',
    component: () => import('@/views/SetupView.vue'),
    meta: { bare: true },
  },
  // Unknown paths fall back to the scope resolver.
  { path: '/:pathMatch(.*)*', redirect: { name: 'home' } },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

/** Send the user to the login screen, remembering where they were so the form can
 * return them there. Called by the API client's 401 interceptor. A no-op if we're
 * already on /login (e.g. the login POST itself 401s on bad credentials). */
export function redirectToLogin(): void {
  const current = router.currentRoute.value
  if (current.name === 'login') return
  void router.push({ name: 'login', query: { redirect: current.fullPath } })
}

// Keep the ui store's active scope in sync with the URL, so a reload on a scoped
// route (or a deep link) restores the selection that drives the switcher and nav.
router.beforeEach(async (to) => {
  // Govern the /login and /setup screens together. Unauthenticated access to
  // other routes is NOT pre-blocked here — that would wrongly bounce desktop/local
  // users (no token, no login). The API client's 401 interceptor sends them to
  // /login only when the server demands it; this guard then forwards to /setup if
  // the server has no admin yet. The setup probe runs only on these two routes
  // (and only when signed out), so there's no cost on the happy path or desktop.
  if (to.name === 'login' || to.name === 'setup') {
    const auth = useAuthStore()
    const needsSetup = auth.isAuthenticated ? false : await fetchSetupNeeded()
    return authRouteTarget(to.name, auth.isAuthenticated, needsSetup, to.query.redirect)
  }

  const ui = useUiStore()
  const investorId = to.params.investorId
  const familyId = to.params.familyId
  if (typeof investorId === 'string' || typeof familyId === 'string') {
    // The scope id comes from the URL — which, via the `/` redirect, is seeded from a
    // localStorage scope the server knows nothing about. Validate it against the roster
    // so a deleted investor (or an emptied DB) self-heals instead of dead-ending on a
    // dashboard for an id that no longer exists.
    const roster = useRosterStore()
    await roster.ensureLoaded()
    const stale = staleScopeRedirect(
      typeof investorId === 'string' ? investorId : undefined,
      typeof familyId === 'string' ? familyId : undefined,
      roster.investors.map((i) => i.id),
      roster.families.map((f) => f.id),
    )
    if (stale) {
      ui.clearScope() // drop the stale persisted scope so `/` stops resolving to it
      return stale
    }
  }
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
