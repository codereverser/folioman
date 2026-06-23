/**
 * Pure form model for authoring a corporate action by hand (the M4 resolution
 * path). Kept out of the .vue so the per-kind validation + payload assembly is
 * unit-tested without mounting PrimeVue dialogs.
 */
import type { ManualCorporateActionBody } from '@/stores/integrity'

export type ManualCaKind = 'bonus' | 'split' | 'merger' | 'rights' | 'buyback'

// Demerger is intentionally absent: it isn't a manually-authored corporate action.
// A demerger is resolved by recording the child's lots as opening lots (the broker
// already allocated their cost) and linking the parent — not through this form.
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
    units: '',
    price: '',
    cpIsin: '',
    cpSymbol: '',
    cpName: '',
  }
}

export const isUnitFactorKind = (k: ManualCaKind): boolean => k === 'bonus' || k === 'split'
export const isCrossSecurityKind = (k: ManualCaKind): boolean => k === 'merger'
export const isRightsOrBuybackKind = (k: ManualCaKind): boolean => k === 'rights' || k === 'buyback'

/** Every field the chosen kind requires is filled. */
export function isManualCaValid(f: ManualCaForm): boolean {
  if (!f.exDate) return false
  if (isUnitFactorKind(f.kind)) return !!f.unitMultiplier
  if (f.kind === 'merger') return !!f.cpIsin.trim() && !!f.mergerRatio
  if (isRightsOrBuybackKind(f.kind)) return !!f.units && !!f.price
  return false
}

/** Build the API payload for the chosen kind. `counterparty_*` are always present
 * (the schema types them non-nullable); only a merger fills them. */
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
    body.merger_ratio = f.mergerRatio
  } else {
    body.units = f.units
    body.price = f.price
  }
  return body
}
