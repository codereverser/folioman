import { afterEach, describe, expect, it, vi } from 'vitest'

import { applyUpdate, setUpdater, updateAvailable } from './updateState'

describe('pwa updateState', () => {
  afterEach(() => {
    updateAvailable.value = false
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('starts with no update pending', () => {
    expect(updateAvailable.value).toBe(false)
  })

  it('applyUpdate runs the wired updater (activate + reload)', async () => {
    const updater = vi.fn().mockResolvedValue(undefined)
    setUpdater(updater)
    await applyUpdate()
    expect(updater).toHaveBeenCalledOnce()
  })

  it('falls back to a plain reload when no service worker is registered', async () => {
    setUpdater(null as unknown as () => Promise<void>)
    const reload = vi.fn()
    vi.stubGlobal('location', { reload })
    await applyUpdate()
    expect(reload).toHaveBeenCalledOnce()
  })
})
