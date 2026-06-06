import { afterEach, describe, expect, it, vi } from 'vitest'
import { isDesktopShell, pickCasFile } from './desktop'

function setBridge(api: unknown): void {
  ;(window as unknown as { pywebview?: unknown }).pywebview = api ? { api } : undefined
}

afterEach(() => {
  delete (window as unknown as { pywebview?: unknown }).pywebview
})

describe('isDesktopShell', () => {
  it('is false in a plain browser (no bridge injected)', () => {
    expect(isDesktopShell()).toBe(false)
  })

  it('is true once PyWebView injects an api with pick_cas_file', () => {
    setBridge({ pick_cas_file: () => Promise.resolve(null) })
    expect(isDesktopShell()).toBe(true)
  })
})

describe('pickCasFile', () => {
  it('returns null in a browser (no bridge)', async () => {
    expect(await pickCasFile()).toBeNull()
  })

  it('returns null when the native dialog is cancelled', async () => {
    setBridge({ pick_cas_file: vi.fn().mockResolvedValue(null) })
    expect(await pickCasFile()).toBeNull()
  })

  it('rebuilds a File from the base64 bytes the bridge returns', async () => {
    const data = btoa('%PDF-1.7 hello') // bridge hands back base64-encoded bytes
    setBridge({ pick_cas_file: vi.fn().mockResolvedValue({ name: 'cas.pdf', data }) })

    const file = await pickCasFile()

    expect(file).toBeInstanceOf(File)
    expect(file?.name).toBe('cas.pdf')
    expect(file?.type).toBe('application/pdf')
    expect(file?.size).toBe('%PDF-1.7 hello'.length) // all bytes decoded, none lost
  })
})
