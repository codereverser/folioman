import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  api,
  unwrap,
  type FolioOut,
  type InvestorIn,
  type InvestorOut,
  type InvestorUpdate,
} from '@/api/client'
import { useRosterStore } from './roster'
import { useUiStore } from './ui'

/**
 * Investor CRUD + per-investor folio lists. Mutations that change the roster
 * (create / delete / move between families) invalidate the roster cache.
 * Failures surface as an error toast; the action resolves to null.
 */
export const useInvestorStore = defineStore('investor', () => {
  const folios = ref<Record<number, FolioOut[]>>({})
  const saving = ref(false)
  const error = ref<string | null>(null)

  async function guard<T>(
    action: () => Promise<T>,
    failSummary: string,
    { invalidateRoster = false }: { invalidateRoster?: boolean } = {},
  ): Promise<T | null> {
    saving.value = true
    error.value = null
    try {
      const result = await action()
      if (invalidateRoster) await useRosterStore().reload()
      return result
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      useUiStore().notify({ severity: 'error', summary: failSummary, detail: error.value })
      return null
    } finally {
      saving.value = false
    }
  }

  function createInvestor(payload: InvestorIn): Promise<InvestorOut | null> {
    return guard(
      async () => unwrap(await api.POST('/api/investors/', { body: payload }), 'create investor failed'),
      'Could not create investor',
      { invalidateRoster: true },
    )
  }

  function updateInvestor(
    investorId: number,
    patch: InvestorUpdate,
  ): Promise<InvestorOut | null> {
    return guard(
      async () =>
        unwrap(
          await api.PATCH('/api/investors/{investor_id}', {
            params: { path: { investor_id: investorId } },
            body: patch,
          }),
          'update investor failed',
        ),
      'Could not update investor',
      { invalidateRoster: true },
    )
  }

  /** Move an investor into a family (or out of one with null). */
  function setFamily(investorId: number, familyId: number | null): Promise<InvestorOut | null> {
    return updateInvestor(investorId, { family_id: familyId })
  }

  function deleteInvestor(investorId: number): Promise<true | null> {
    return guard(
      async () => {
        const { error: apiError } = await api.DELETE('/api/investors/{investor_id}', {
          params: { path: { investor_id: investorId } },
        })
        if (apiError) throw new Error('delete investor failed')
        return true as const
      },
      'Could not delete investor',
      { invalidateRoster: true },
    )
  }

  /** Load (and cache) the folio list for one investor. */
  async function loadFolios(investorId: number): Promise<FolioOut[]> {
    const result = await guard(
      async () =>
        unwrap(
          await api.GET('/api/investors/{investor_id}/folios', {
            params: { path: { investor_id: investorId } },
          }),
          'folios request failed',
        ),
      'Could not load folios',
    )
    const list = result ?? []
    folios.value = { ...folios.value, [investorId]: list }
    return list
  }

  return {
    folios,
    saving,
    error,
    createInvestor,
    updateInvestor,
    setFamily,
    deleteInvestor,
    loadFolios,
  }
})
