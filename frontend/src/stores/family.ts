import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api, unwrap, type FamilyOut } from '@/api/client'
import { useRosterStore } from './roster'
import { useUiStore } from './ui'

/**
 * Family CRUD. Every successful mutation invalidates the roster cache so the
 * switcher and Investors page reflect the change immediately. Failures surface
 * as an error toast and leave `error` set; the action resolves to null.
 */
export const useFamilyStore = defineStore('family', () => {
  const saving = ref(false)
  const error = ref<string | null>(null)

  async function guard<T>(action: () => Promise<T>, failSummary: string): Promise<T | null> {
    saving.value = true
    error.value = null
    try {
      const result = await action()
      await useRosterStore().reload()
      return result
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      useUiStore().notify({ severity: 'error', summary: failSummary, detail: error.value })
      return null
    } finally {
      saving.value = false
    }
  }

  function createFamily(name: string): Promise<FamilyOut | null> {
    return guard(
      async () =>
        unwrap(await api.POST('/api/families/', { body: { name } }), 'create family failed'),
      'Could not create family',
    )
  }

  function renameFamily(familyId: number, name: string): Promise<FamilyOut | null> {
    return guard(
      async () =>
        unwrap(
          await api.PATCH('/api/families/{family_id}', {
            params: { path: { family_id: familyId } },
            body: { name },
          }),
          'rename family failed',
        ),
      'Could not rename family',
    )
  }

  function deleteFamily(familyId: number): Promise<true | null> {
    return guard(async () => {
      const { error: apiError } = await api.DELETE('/api/families/{family_id}', {
        params: { path: { family_id: familyId } },
      })
      if (apiError) throw new Error('delete family failed')
      return true as const
    }, 'Could not delete family')
  }

  return { saving, error, createFamily, renameFamily, deleteFamily }
})
