import { describe, it, expect } from 'vitest'
import { blockedOnMobile } from './index'

describe('blockedOnMobile', () => {
  it('blocks a desktop-only route only on a mobile viewport', () => {
    expect(blockedOnMobile({ desktopOnly: true }, true)).toBe(true)
    expect(blockedOnMobile({ desktopOnly: true }, false)).toBe(false)
  })

  it('never blocks a view-only route', () => {
    expect(blockedOnMobile({}, true)).toBe(false)
    expect(blockedOnMobile({ desktopOnly: false }, true)).toBe(false)
  })
})
