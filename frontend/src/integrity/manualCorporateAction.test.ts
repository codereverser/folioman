import { describe, expect, it } from 'vitest'
import {
  emptyManualCaForm,
  isManualCaValid,
  toManualCaBody,
  type ManualCaForm,
} from './manualCorporateAction'

function form(overrides: Partial<ManualCaForm>): ManualCaForm {
  return { ...emptyManualCaForm('2024-01-01'), ...overrides }
}

describe('isManualCaValid', () => {
  it('requires an ex date for every kind', () => {
    expect(isManualCaValid(form({ kind: 'bonus', exDate: '', unitMultiplier: '2' }))).toBe(false)
  })

  it('bonus/split need a unit multiplier', () => {
    expect(isManualCaValid(form({ kind: 'bonus' }))).toBe(false)
    expect(isManualCaValid(form({ kind: 'split', unitMultiplier: '0.1' }))).toBe(true)
  })

  it('merger needs counterparty ISIN + ratio', () => {
    expect(isManualCaValid(form({ kind: 'merger', mergerRatio: '1.5' }))).toBe(false)
    expect(
      isManualCaValid(form({ kind: 'merger', mergerRatio: '1.5', cpIsin: 'INE040A01034' })),
    ).toBe(true)
  })

  it('rights/buyback need units + price', () => {
    expect(isManualCaValid(form({ kind: 'rights', units: '10' }))).toBe(false)
    expect(isManualCaValid(form({ kind: 'buyback', units: '10', price: '500' }))).toBe(true)
  })
})

describe('toManualCaBody', () => {
  it('bonus carries only the unit multiplier (counterparty fields blank)', () => {
    const b = toManualCaBody(form({ kind: 'bonus', unitMultiplier: '4' }))
    expect(b).toMatchObject({
      kind: 'bonus',
      ex_date: '2024-01-01',
      unit_multiplier: '4',
      counterparty_isin: '',
    })
    expect(b.merger_ratio).toBeUndefined()
  })

  it('merger uppercases the counterparty ISIN/symbol and carries the ratio', () => {
    const b = toManualCaBody(
      form({
        kind: 'merger',
        mergerRatio: '1.5',
        cpIsin: ' ine040a01034 ',
        cpSymbol: ' newco ',
        cpName: ' New Co ',
      }),
    )
    expect(b).toMatchObject({
      kind: 'merger',
      merger_ratio: '1.5',
      counterparty_isin: 'INE040A01034',
      counterparty_symbol: 'NEWCO',
      counterparty_name: 'New Co',
    })
  })

  it('rights/buyback carry units + price', () => {
    const b = toManualCaBody(form({ kind: 'rights', units: '10', price: '500' }))
    expect(b).toMatchObject({ kind: 'rights', units: '10', price: '500' })
  })
})
