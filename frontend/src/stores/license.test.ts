import { describe, it, expect, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useLicenseStore } from './license'

describe('license store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('is free and ungated in this release — every feature is available', () => {
    const license = useLicenseStore()
    expect(license.tier).toBe('free')
    expect(license.isFree).toBe(true)
    expect(license.has('tax_pack')).toBe(true)
    expect(license.has('anything_at_all')).toBe(true)
  })

  it('load marks the store initialised without a backend', async () => {
    const license = useLicenseStore()
    expect(license.loaded).toBe(false)
    await license.load()
    expect(license.loaded).toBe(true)
  })
})
