import { describe, it, expect, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { useUiStore } from './ui'

describe('ui store', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('scope is mutually exclusive — selecting an investor clears the family', () => {
    const ui = useUiStore()
    ui.selectFamily(7)
    expect(ui.selectedFamilyId).toBe(7)
    expect(ui.selectedInvestorId).toBeNull()

    ui.selectInvestor(3)
    expect(ui.selectedInvestorId).toBe(3)
    expect(ui.selectedFamilyId).toBeNull()
  })

  it('clearScope resets both selections', () => {
    const ui = useUiStore()
    ui.selectInvestor(3)
    ui.clearScope()
    expect(ui.selectedInvestorId).toBeNull()
    expect(ui.selectedFamilyId).toBeNull()
  })

  it('persists the scope to localStorage for reload survival', async () => {
    const ui = useUiStore()
    ui.selectInvestor(42)
    await nextTick()
    expect(JSON.parse(localStorage.getItem('folioman.scope') ?? '{}')).toEqual({
      investorId: 42,
      familyId: null,
    })
  })

  it('restores a persisted scope on init', () => {
    localStorage.setItem('folioman.scope', JSON.stringify({ investorId: null, familyId: 9 }))
    setActivePinia(createPinia())
    const ui = useUiStore()
    expect(ui.selectedFamilyId).toBe(9)
    expect(ui.selectedInvestorId).toBeNull()
  })

  it('queues and drains toasts', () => {
    const ui = useUiStore()
    ui.notify({ severity: 'success', summary: 'Saved' })
    expect(ui.toasts.length).toBe(1)
    const drained = ui.drainToasts()
    expect(drained).toHaveLength(1)
    expect(ui.toasts.length).toBe(0)
  })
})
