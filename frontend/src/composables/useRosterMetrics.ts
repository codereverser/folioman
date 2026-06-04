import { ref } from 'vue'
import { api } from '@/api/client'

export interface FamilyAggregate {
  totalInr: string
  staleCount: number
  investorCount: number
  needsAttentionCount: number
  asOf: string
}

export interface InvestorSummary {
  totalInr: string
  holdingsCount: number
  /** (security, folio) reconciliation units — the tax-ready fraction's denominator. */
  integrityUnitCount: number
  taxReadyCount: number
  needsAttentionCount: number
  snapshotCount: number
  /** Held funds with no NAV — the total silently excludes them (the fixable gap). */
  unpricedFundCount: number
  lastImportAt: string | null
}

/**
 * Lazily-fetched, cached roster roll-ups: a family's combined value
 * (`/families/{id}/aggregate`) and an investor's headline summary — current INR
 * value, tax-ready split, and last import (`/investors/{id}/summary`). Fetched
 * on demand (panel expand) so the roster never N+1-fetches up front. Fails soft:
 * a failed call leaves the entry unset and the UI shows a dash.
 */
export interface RosterAggregate {
  totalInr: string
  investorCount: number
  familyCount: number
  integrityUnitCount: number
  taxReadyCount: number
  needsAttentionCount: number
  snapshotCount: number
  asOf: string
}

export function useRosterMetrics() {
  const familyAggregates = ref<Record<number, FamilyAggregate>>({})
  const investorSummaries = ref<Record<number, InvestorSummary>>({})
  const rosterAggregate = ref<RosterAggregate | null>(null)
  const pending = ref<Record<string, boolean>>({})

  /** Advisor-wide header totals — one call on page load (no per-investor N+1). */
  async function loadRosterAggregate(): Promise<void> {
    if (rosterAggregate.value || pending.value['roster']) return
    pending.value = { ...pending.value, roster: true }
    try {
      const { data, error } = await api.GET('/api/investors/aggregate')
      if (!error && data) {
        rosterAggregate.value = {
          totalInr: data.total_inr,
          investorCount: data.investor_count,
          familyCount: data.family_count,
          integrityUnitCount: data.integrity_unit_count,
          taxReadyCount: data.tax_ready_count,
          needsAttentionCount: data.needs_attention_count,
          snapshotCount: data.snapshot_count,
          asOf: data.as_of,
        }
      }
    } finally {
      pending.value = { ...pending.value, roster: false }
    }
  }

  async function loadFamilyAggregate(familyId: number): Promise<void> {
    const key = `f:${familyId}`
    if (familyAggregates.value[familyId] || pending.value[key]) return
    pending.value = { ...pending.value, [key]: true }
    try {
      const { data, error } = await api.GET('/api/families/{family_id}/aggregate', {
        params: { path: { family_id: familyId } },
      })
      if (!error && data) {
        familyAggregates.value = {
          ...familyAggregates.value,
          [familyId]: {
            totalInr: data.total_inr,
            staleCount: data.stale_count,
            investorCount: data.investor_count,
            needsAttentionCount: data.needs_attention_count,
            asOf: data.as_of,
          },
        }
      }
    } finally {
      pending.value = { ...pending.value, [key]: false }
    }
  }

  async function loadInvestorSummary(investorId: number): Promise<void> {
    const key = `i:${investorId}`
    if (investorSummaries.value[investorId] || pending.value[key]) return
    pending.value = { ...pending.value, [key]: true }
    try {
      const { data, error } = await api.GET('/api/investors/{investor_id}/summary', {
        params: { path: { investor_id: investorId } },
      })
      if (!error && data) {
        investorSummaries.value = {
          ...investorSummaries.value,
          [investorId]: {
            totalInr: data.total_inr,
            holdingsCount: data.holdings_count,
            integrityUnitCount: data.integrity_unit_count,
            taxReadyCount: data.tax_ready_count,
            needsAttentionCount: data.needs_attention_count,
            snapshotCount: data.snapshot_count,
            unpricedFundCount: data.unpriced_fund_count,
            lastImportAt: data.last_import_at,
          },
        }
      }
    } finally {
      pending.value = { ...pending.value, [key]: false }
    }
  }

  return {
    familyAggregates,
    investorSummaries,
    rosterAggregate,
    loadFamilyAggregate,
    loadInvestorSummary,
    loadRosterAggregate,
  }
}
