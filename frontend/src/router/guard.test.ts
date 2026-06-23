import { describe, it, expect } from 'vitest'
import { blockedOnMobile, authRouteTarget, staleScopeRedirect } from './index'

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

describe('staleScopeRedirect', () => {
  it('lets a known investor scope through', () => {
    expect(staleScopeRedirect('3', undefined, [1, 3], [])).toBeNull()
  })

  it('lets a known family scope through', () => {
    expect(staleScopeRedirect(undefined, '2', [], [2, 5])).toBeNull()
  })

  it('bounces an investor id the roster does not have to the roster', () => {
    // e.g. the DB was emptied but localStorage kept investor 1.
    expect(staleScopeRedirect('1', undefined, [], [])).toEqual({ name: 'investors' })
    expect(staleScopeRedirect('1', undefined, [2, 3], [])).toEqual({ name: 'investors' })
  })

  it('bounces an unknown family id to the roster', () => {
    expect(staleScopeRedirect(undefined, '9', [], [1])).toEqual({ name: 'investors' })
  })

  it('ignores a route with no scope params', () => {
    expect(staleScopeRedirect(undefined, undefined, [], [])).toBeNull()
  })
})
