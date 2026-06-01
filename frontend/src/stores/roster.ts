import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api/client'

export interface RosterInvestor {
  id: number
  name: string
  familyId: number | null
}

export interface RosterFamily {
  id: number
  name: string
}

export interface RosterGroup {
  /** The family this group represents, or null for the "Unaffiliated" group. */
  family: RosterFamily | null
  investors: RosterInvestor[]
}

const UNAFFILIATED_LABEL = 'Unaffiliated'

// Dev/offline fallback so the shell stays demoable without a backend — mirrors
// the seeded useDashboard / useIntegrity composables.
const SEED_FAMILIES: RosterFamily[] = [
  { id: 1, name: 'Sharma Family' },
  { id: 2, name: 'Iyer Family' },
]
const SEED_INVESTORS: RosterInvestor[] = [
  { id: 10, name: 'Rajesh Sharma', familyId: 1 },
  { id: 11, name: 'Priya Sharma', familyId: 1 },
  { id: 12, name: 'Meena Iyer', familyId: 2 },
  { id: 20, name: 'Anil Kumar', familyId: null },
]

/**
 * Cached roster (investors + families) for the switcher and the Investors page.
 * Loaded once and reused across navigation; CRUD in the investor / family stores
 * invalidates it via {@link reload}. On a load failure it falls back to seed data
 * so the app stays navigable without a backend in dev.
 */
export const useRosterStore = defineStore('roster', () => {
  const families = ref<RosterFamily[]>([])
  const investors = ref<RosterInvestor[]>([])
  const loading = ref(false)
  const loaded = ref(false)
  const error = ref<string | null>(null)
  const usingSeed = ref(false)

  async function load(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const [fam, inv] = await Promise.all([api.GET('/api/families/'), api.GET('/api/investors/')])
      if (fam.error || !fam.data || inv.error || !inv.data) {
        throw new Error('roster request failed')
      }
      families.value = fam.data.map((f) => ({ id: f.id, name: f.name }))
      investors.value = inv.data.map((i) => ({ id: i.id, name: i.name, familyId: i.family_id }))
      usingSeed.value = false
      loaded.value = true
    } catch (e) {
      // Fail soft to seed data: the advisor shell remains usable offline / in dev.
      families.value = [...SEED_FAMILIES]
      investors.value = [...SEED_INVESTORS]
      usingSeed.value = true
      loaded.value = true
      error.value = e instanceof Error ? e.message : 'unknown error'
    } finally {
      loading.value = false
    }
  }

  /** Load once; subsequent calls are no-ops while a load is in flight or done. */
  async function ensureLoaded(): Promise<void> {
    if (loaded.value || loading.value) return
    await load()
  }

  /** Force a refetch — called after any investor/family CRUD. */
  async function reload(): Promise<void> {
    loaded.value = false
    await load()
  }

  /** Investors grouped by family, with solo investors under "Unaffiliated" last. */
  const groups = computed<RosterGroup[]>(() => {
    const byFamily: RosterGroup[] = families.value.map((family) => ({
      family,
      investors: investors.value.filter((inv) => inv.familyId === family.id),
    }))
    const unaffiliated = investors.value.filter((inv) => inv.familyId === null)
    if (unaffiliated.length > 0) {
      byFamily.push({ family: null, investors: unaffiliated })
    }
    return byFamily
  })

  const isEmpty = computed(() => loaded.value && investors.value.length === 0)

  function investorName(id: number | null): string | null {
    if (id === null) return null
    return investors.value.find((inv) => inv.id === id)?.name ?? null
  }

  function familyName(id: number | null): string | null {
    if (id === null) return null
    return families.value.find((fam) => fam.id === id)?.name ?? null
  }

  return {
    families,
    investors,
    groups,
    loading,
    loaded,
    error,
    usingSeed,
    isEmpty,
    load,
    ensureLoaded,
    reload,
    investorName,
    familyName,
    UNAFFILIATED_LABEL,
  }
})
