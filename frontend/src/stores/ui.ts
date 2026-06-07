import { computed, readonly, ref, watch } from 'vue'
import { defineStore } from 'pinia'

const SCOPE_STORAGE_KEY = 'folioman.scope'
const THEME_STORAGE_KEY = 'folioman.theme'
const SIDEBAR_STORAGE_KEY = 'folioman.sidebar'
const MOBILE_BREAKPOINT = 768
const TABLET_MAX = 1024

export type ToastSeverity = 'success' | 'info' | 'warn' | 'error'
export type ThemePreference = 'light' | 'dark' | 'system'

function loadThemePreference(): ThemePreference {
  if (typeof localStorage === 'undefined') return 'system'
  const raw = localStorage.getItem(THEME_STORAGE_KEY)
  return raw === 'light' || raw === 'dark' || raw === 'system' ? raw : 'system'
}

/** Persisted sidebar preference: `null` = follow the viewport (no explicit choice). */
function loadSidebarPref(): boolean | null {
  if (typeof localStorage === 'undefined') return null
  const raw = localStorage.getItem(SIDEBAR_STORAGE_KEY)
  return raw === 'collapsed' ? true : raw === 'expanded' ? false : null
}

function systemPrefersDark(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)').matches
    : false
}

export interface UiToast {
  severity: ToastSeverity
  summary: string
  detail?: string
  life?: number
}

interface PersistedScope {
  investorId: number | null
  familyId: number | null
}

function loadScope(): PersistedScope {
  if (typeof localStorage === 'undefined') return { investorId: null, familyId: null }
  try {
    const raw = localStorage.getItem(SCOPE_STORAGE_KEY)
    if (!raw) return { investorId: null, familyId: null }
    const parsed = JSON.parse(raw) as Partial<PersistedScope>
    return {
      investorId: typeof parsed.investorId === 'number' ? parsed.investorId : null,
      familyId: typeof parsed.familyId === 'number' ? parsed.familyId : null,
    }
  } catch {
    return { investorId: null, familyId: null }
  }
}

/**
 * Cross-cutting UI state: global loading flag, a toast queue drained by the app
 * shell, the active scope (an investor OR a family — never both), and a viewport
 * width flag used by view-only routes to drop to single-column layouts.
 */
export const useUiStore = defineStore('ui', () => {
  // --- global loading -------------------------------------------------------
  const loading = ref(false)
  function setLoading(value: boolean): void {
    loading.value = value
  }
  async function withLoading<T>(fn: () => Promise<T>): Promise<T> {
    loading.value = true
    try {
      return await fn()
    } finally {
      loading.value = false
    }
  }

  // --- toast queue (drained by App.vue → PrimeVue Toast) --------------------
  const toasts = ref<UiToast[]>([])
  function notify(toast: UiToast): void {
    toasts.value.push(toast)
  }
  function drainToasts(): UiToast[] {
    const pending = toasts.value
    toasts.value = []
    return pending
  }

  // Tell the user (once per session) that stale NAVs are being refreshed in the
  // background — the launch catch-up kicks the refresh; this just sets expectation.
  // Guarded so switching investors / remounting the dashboard doesn't re-toast.
  let navRefreshNotified = false
  function notifyNavRefreshOnce(): void {
    if (navRefreshNotified) return
    navRefreshNotified = true
    notify({
      severity: 'info',
      summary: 'Updating prices',
      detail: 'Fetching the latest NAVs in the background — values will refresh shortly.',
      life: 6000,
    })
  }

  // --- active scope: investor XOR family ------------------------------------
  const initial = loadScope()
  const selectedInvestorId = ref<number | null>(initial.investorId)
  const selectedFamilyId = ref<number | null>(initial.familyId)

  function selectInvestor(id: number): void {
    selectedInvestorId.value = id
    selectedFamilyId.value = null // mutually exclusive
  }
  function selectFamily(id: number): void {
    selectedFamilyId.value = id
    selectedInvestorId.value = null // mutually exclusive
  }
  function clearScope(): void {
    selectedInvestorId.value = null
    selectedFamilyId.value = null
  }

  // Persist the scope so the switcher survives a reload.
  watch([selectedInvestorId, selectedFamilyId], ([investorId, familyId]) => {
    if (typeof localStorage === 'undefined') return
    localStorage.setItem(SCOPE_STORAGE_KEY, JSON.stringify({ investorId, familyId }))
  })

  // --- theme (light / dark / follow-system) ---------------------------------
  const themePreference = ref<ThemePreference>(loadThemePreference())
  // The resolved scheme actually applied (`system` collapses to light/dark).
  const isDark = ref(
    themePreference.value === 'dark' ||
      (themePreference.value === 'system' && systemPrefersDark()),
  )

  function applyTheme(): void {
    isDark.value =
      themePreference.value === 'dark' ||
      (themePreference.value === 'system' && systemPrefersDark())
    if (typeof document !== 'undefined') {
      document.documentElement.classList.toggle('dark', isDark.value)
    }
  }

  function setTheme(pref: ThemePreference): void {
    themePreference.value = pref
    if (typeof localStorage !== 'undefined') localStorage.setItem(THEME_STORAGE_KEY, pref)
    applyTheme()
  }

  function toggleTheme(): void {
    // Cycle through the three explicit states so `system` stays reachable.
    setTheme(themePreference.value === 'light' ? 'dark' : themePreference.value === 'dark' ? 'system' : 'light')
  }

  /** Apply the saved theme and re-resolve when the OS scheme changes in `system` mode. */
  function startThemeTracking(): () => void {
    applyTheme()
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {}
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (): void => {
      if (themePreference.value === 'system') applyTheme()
    }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }

  // --- responsive flag ------------------------------------------------------
  const viewportWidth = ref(typeof window === 'undefined' ? 1280 : window.innerWidth)
  const isMobile = computed(() => viewportWidth.value < MOBILE_BREAKPOINT)

  function onResize(): void {
    viewportWidth.value = window.innerWidth
  }
  function startViewportTracking(): () => void {
    if (typeof window === 'undefined') return () => {}
    window.addEventListener('resize', onResize)
    onResize()
    return () => window.removeEventListener('resize', onResize)
  }

  // --- sidebar collapse (icon rail) -----------------------------------------
  // Tri-state: an explicit user toggle pins `collapsed`/`expanded` (persisted);
  // with no choice yet (`null`) the rail follows the viewport — the 768–1024
  // tablet band defaults to the icon rail, wider desktops to the full sidebar
  // (DESIGN-SYSTEM §5). Mobile (<768) ignores this entirely (drawer + bottom tabs).
  const sidebarPref = ref<boolean | null>(loadSidebarPref())
  const isTablet = computed(
    () => viewportWidth.value >= MOBILE_BREAKPOINT && viewportWidth.value < TABLET_MAX,
  )
  const sidebarCollapsed = computed(() =>
    sidebarPref.value === null ? isTablet.value : sidebarPref.value,
  )
  function setSidebarCollapsed(value: boolean): void {
    sidebarPref.value = value
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(SIDEBAR_STORAGE_KEY, value ? 'collapsed' : 'expanded')
    }
  }
  function toggleSidebar(): void {
    setSidebarCollapsed(!sidebarCollapsed.value)
  }

  return {
    loading: readonly(loading),
    setLoading,
    withLoading,
    toasts: readonly(toasts),
    notify,
    notifyNavRefreshOnce,
    drainToasts,
    selectedInvestorId,
    selectedFamilyId,
    selectInvestor,
    selectFamily,
    clearScope,
    themePreference: readonly(themePreference),
    isDark: readonly(isDark),
    setTheme,
    toggleTheme,
    startThemeTracking,
    isMobile,
    startViewportTracking,
    sidebarCollapsed,
    toggleSidebar,
    setSidebarCollapsed,
  }
})
