import { describe, it, expect } from 'vitest'
import { blockedOnMobile, authRouteTarget } from './index'

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

describe('authRouteTarget', () => {
  it('lets a signed-out visitor see the login form when no setup is pending', () => {
    expect(authRouteTarget('login', false, false, '/investors')).toBe(true)
  })

  it('forces a signed-out visitor to /setup when the server needs its first admin', () => {
    expect(authRouteTarget('login', false, true, undefined)).toEqual({ name: 'setup' })
    expect(authRouteTarget('setup', false, true, undefined)).toBe(true) // already there
  })

  it('bounces /setup → /login when no setup is pending', () => {
    expect(authRouteTarget('setup', false, false, undefined)).toEqual({ name: 'login' })
  })

  it('sends a signed-in visitor on /login back to their saved target', () => {
    expect(authRouteTarget('login', true, false, '/investors/3/dashboard')).toBe(
      '/investors/3/dashboard',
    )
  })

  it('sends a signed-in visitor home when there is no usable redirect', () => {
    expect(authRouteTarget('login', true, false, '')).toEqual({ name: 'home' })
    expect(authRouteTarget('login', true, false, ['/a'])).toEqual({ name: 'home' })
    expect(authRouteTarget('setup', true, false, undefined)).toEqual({ name: 'home' })
  })
})
