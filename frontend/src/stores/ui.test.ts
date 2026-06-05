import { describe, it, expect, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { useUiStore } from './ui'

describe('ui store', () => {
  beforeEach(() => {
    localStorage.clear()
    Object.defineProperty(window, 'innerWidth', { value: 1024, configurable: true })
    setActivePinia(createPinia())
  })

  function freshStore(width: number) {
    Object.defineProperty(window, 'innerWidth', { value: width, configurable: true })
    setActivePinia(createPinia())
    return useUiStore()
  }

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

  it('sidebar defaults to expanded on desktop width (no saved pref)', () => {
    const ui = freshStore(1280)
    expect(ui.sidebarCollapsed).toBe(false)
  })

  it('sidebar defaults to the icon rail on tablet width (768–1024)', () => {
    const ui = freshStore(900)
    expect(ui.sidebarCollapsed).toBe(true)
  })

  it('toggleSidebar pins and persists the choice, overriding the viewport default', () => {
    const ui = freshStore(900) // tablet → defaults collapsed
    expect(ui.sidebarCollapsed).toBe(true)
    ui.toggleSidebar() // explicit expand sticks
    expect(ui.sidebarCollapsed).toBe(false)
    expect(localStorage.getItem('folioman.sidebar')).toBe('expanded')
  })

  it('restores a persisted collapsed sidebar on init regardless of width', () => {
    localStorage.setItem('folioman.sidebar', 'collapsed')
    const ui = freshStore(1440) // wide desktop would otherwise expand
    expect(ui.sidebarCollapsed).toBe(true)
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
