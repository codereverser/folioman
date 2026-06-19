/**
 * Pure form model for authoring a corporate action by hand (the M4 resolution
 * path). Kept out of the .vue so the per-kind validation + payload assembly is
 * unit-tested without mounting PrimeVue dialogs.
 */
import type { ManualCorporateActionBody } from '@/stores/integrity'

export type ManualCaKind = 'bonus' | 'split' | 'merger' | 'demerger' | 'rights' | 'buyback'

// Demerger is intentionally absent: its lot-splitting persistence (a partially
// consumed parent lot becomes two derived rows, and multiple open lots become
// multiple child receipts) isn't safely represented yet, so it stays out of the
// manual picker until that lands. The form/validation/payload still understand it.
export const MANUAL_CA_KINDS: { label: string; value: ManualCaKind }[] = [
  { label: 'Bonus', value: 'bonus' },
  { label: 'Split', value: 'split' },
  { label: 'Merger', value: 'merger' },
  { label: 'Rights issue', value: 'rights' },
  { label: 'Buyback', value: 'buyback' },
]

export interface ManualCaForm {
  kind: ManualCaKind
  exDate: string
  unitMultiplier: string
  mergerRatio: string
  childRatio: string
  childCostFraction: string
  units: string
  price: string
  cpIsin: string
  cpSymbol: string
  cpName: string
}

export function emptyManualCaForm(exDate = ''): ManualCaForm {
  return {
    kind: 'bonus',
    exDate,
    unitMultiplier: '',
    mergerRatio: '',
    childRatio: '',
    childCostFraction: '',
    units: '',
    price: '',
    cpIsin: '',
    cpSymbol: '',
    cpName: '',
  }
}

export const isUnitFactorKind = (k: ManualCaKind): boolean => k === 'bonus' || k === 'split'
export const isCrossSecurityKind = (k: ManualCaKind): boolean => k === 'merger' || k === 'demerger'
export const isRightsOrBuybackKind = (k: ManualCaKind): boolean => k === 'rights' || k === 'buyback'

/** Every field the chosen kind requires is filled. */
export function isManualCaValid(f: ManualCaForm): boolean {
  if (!f.exDate) return false
  if (isUnitFactorKind(f.kind)) return !!f.unitMultiplier
  if (f.kind === 'merger') return !!f.cpIsin.trim() && !!f.mergerRatio
  if (f.kind === 'demerger') return !!f.cpIsin.trim() && !!f.childRatio && !!f.childCostFraction
  if (isRightsOrBuybackKind(f.kind)) return !!f.units && !!f.price
  return false
}

/** Build the API payload for the chosen kind. `counterparty_*` are always present
 * (the schema types them non-nullable); only a merger/demerger fills them. */
export function toManualCaBody(f: ManualCaForm): ManualCorporateActionBody {
  const body: ManualCorporateActionBody = {
    kind: f.kind,
    ex_date: f.exDate,
    counterparty_isin: '',
    counterparty_symbol: '',
    counterparty_name: '',
  }
  if (isUnitFactorKind(f.kind)) {
    body.unit_multiplier = f.unitMultiplier
  } else if (isCrossSecurityKind(f.kind)) {
    body.counterparty_isin = f.cpIsin.trim().toUpperCase()
    body.counterparty_symbol = f.cpSymbol.trim().toUpperCase()
    body.counterparty_name = f.cpName.trim()
    if (f.kind === 'merger') body.merger_ratio = f.mergerRatio
    else {
      body.child_ratio = f.childRatio
      body.child_cost_fraction = f.childCostFraction
    }
  } else {
    body.units = f.units
    body.price = f.price
  }
  return body
}
