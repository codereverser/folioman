import { describe, it, expect, vi, beforeEach } from 'vitest'

// Stub openapi-fetch so the real `importCas`/`unwrap` run against a spy client.
// `vi.hoisted` defines the spy in the same hoisted scope as `vi.mock`.
const { post } = vi.hoisted(() => ({ post: vi.fn() }))
vi.mock('openapi-fetch', () => ({ default: () => ({ POST: post, GET: vi.fn() }) }))

import { importCas } from '@/api/client'

function serialize(call: unknown[]): FormData {
  const init = call[1] as { body: unknown; bodySerializer: (b: unknown) => FormData }
  return init.bodySerializer(init.body)
}

describe('importCas', () => {
  beforeEach(() => post.mockReset())

  it('uploads multipart form-data and returns the job', async () => {
    post.mockResolvedValue({ data: { id: 7, status: 'success', result: { detected: 'mf_cas' } } })
    const file = new File([new Uint8Array([1, 2, 3])], 'cas.pdf', { type: 'application/pdf' })

    const job = await importCas(42, file, 'secret', false)

    expect(job.id).toBe(7)
    const [path, init] = post.mock.calls[0] as [string, { params: { path: { investor_id: number } } }]
    expect(path).toBe('/api/investors/{investor_id}/imports/cas')
    expect(init.params.path.investor_id).toBe(42)

    const fd = serialize(post.mock.calls[0])
    expect(fd).toBeInstanceOf(FormData)
    expect(fd.get('file')).toBeInstanceOf(File)
    expect(fd.get('password')).toBe('secret')
    expect(fd.get('confirm')).toBe('false')
  })

  it('omits a blank password and forwards confirm=true', async () => {
    post.mockResolvedValue({ data: { id: 8, status: 'needs_confirmation', result: {} } })
    const file = new File([new Uint8Array([1])], 'ecas.pdf')

    await importCas(1, file, '', true)

    const fd = serialize(post.mock.calls[0])
    expect(fd.get('password')).toBeNull()
    expect(fd.get('confirm')).toBe('true')
  })

  it('throws when the response carries a transport error', async () => {
    post.mockResolvedValue({ error: { detail: 'bad pdf' } })
    const file = new File([new Uint8Array([1])], 'x.pdf')

    await expect(importCas(1, file)).rejects.toThrow('Import failed')
  })
})
