import { describe, it, expect, vi, beforeEach } from 'vitest'

// Stub openapi-fetch so the real `importCas`/`previewCas`/`unwrap` run against a
// spy client. `vi.hoisted` defines the spy in the same hoisted scope as `vi.mock`.
const { post } = vi.hoisted(() => ({ post: vi.fn() }))
// `use` is stubbed to complete the openapi-fetch shape; the auth interceptor now
// lives in api/authInterceptor.ts (registered from main.ts), not imported here.
vi.mock('openapi-fetch', () => ({ default: () => ({ POST: post, GET: vi.fn(), use: vi.fn() }) }))

import { importCas, previewCas } from '@/api/client'

function serialize(call: unknown[]): FormData {
  const init = call[1] as { body: unknown; bodySerializer: (b: unknown) => FormData }
  return init.bodySerializer(init.body)
}

describe('importCas', () => {
  beforeEach(() => post.mockReset())

  it('posts to the advisor-level endpoint and returns the job', async () => {
    post.mockResolvedValue({
      data: { id: 7, investor_id: 42, status: 'success', result: { detected: 'mf_cas' } },
    })
    const file = new File([new Uint8Array([1, 2, 3])], 'cas.pdf', { type: 'application/pdf' })

    const job = await importCas(file, 'secret', false)

    expect(job.id).toBe(7)
    const [path] = post.mock.calls[0] as [string]
    expect(path).toBe('/api/imports/cas') // no investor_id in the path

    const fd = serialize(post.mock.calls[0])
    expect(fd).toBeInstanceOf(FormData)
    expect(fd.get('file')).toBeInstanceOf(File)
    expect(fd.get('password')).toBe('secret')
    expect(fd.get('confirm')).toBe('false')
  })

  it('omits a blank password and forwards confirm=true', async () => {
    post.mockResolvedValue({ data: { id: 8, status: 'needs_confirmation', result: {} } })
    const file = new File([new Uint8Array([1])], 'ecas.pdf')

    await importCas(file, '', true)

    const fd = serialize(post.mock.calls[0])
    expect(fd.get('password')).toBeNull()
    expect(fd.get('confirm')).toBe('true')
  })

  it('throws when the response carries a transport error', async () => {
    post.mockResolvedValue({ error: { detail: 'bad pdf' } })
    const file = new File([new Uint8Array([1])], 'x.pdf')

    await expect(importCas(file)).rejects.toThrow('Import failed')
  })
})

describe('previewCas', () => {
  beforeEach(() => post.mockReset())

  it('posts the file to the preview endpoint and returns the identity', async () => {
    post.mockResolvedValue({
      data: { kind: 'mf_cas', investor_name: 'Asha Rao', pan_masked: 'XXXXXX234F', email: '' },
    })
    const file = new File([new Uint8Array([1])], 'cas.pdf')

    const preview = await previewCas(file, 'pw')

    expect(preview.investor_name).toBe('Asha Rao')
    const [path] = post.mock.calls[0] as [string]
    expect(path).toBe('/api/imports/cas/preview')
    const fd = serialize(post.mock.calls[0])
    expect(fd.get('file')).toBeInstanceOf(File)
    expect(fd.get('password')).toBe('pw')
    expect(fd.get('confirm')).toBeNull() // preview never sends confirm
  })

  it('throws a labelled error on a rejected statement', async () => {
    post.mockResolvedValue({ error: { detail: 'no PAN' } })
    const file = new File([new Uint8Array([1])], 'x.pdf')

    await expect(previewCas(file)).rejects.toThrow('Could not read this statement')
  })
})
