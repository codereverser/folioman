import { describe, it, expect } from 'vitest'
import { toCsv } from './csv'

describe('toCsv', () => {
  it('emits a header row then one row per record, keyed by column', () => {
    const csv = toCsv(
      ['a', 'b'],
      [
        { a: '1', b: '2' },
        { a: '3', b: '4' },
      ],
    )
    expect(csv).toBe('a,b\r\n1,2\r\n3,4')
  })

  it('quotes fields containing commas, quotes, or newlines and doubles inner quotes', () => {
    const csv = toCsv(['name', 'note'], [{ name: 'Acme, Inc', note: 'say "hi"\nbye' }])
    expect(csv).toBe('name,note\r\n"Acme, Inc","say ""hi""\nbye"')
  })

  it('renders a missing cell as an empty field', () => {
    expect(toCsv(['a', 'b'], [{ a: '1' }])).toBe('a,b\r\n1,')
  })
})
