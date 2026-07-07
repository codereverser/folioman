import { computed, ref, type Ref } from 'vue'
import { api, type Schemas } from '@/api/client'
import { useIntegrityStore } from '@/stores/integrity'
import { currentFy, fyOptions } from '@/utils/fy'

export type IncomeReport = Schemas['IncomeReportOut']
export type IncomeKindGroup = Schemas['IncomeKindGroup']
export type IncomeRow = Schemas['IncomeRow']
export type IncomeFyPoint = Schemas['IncomeFyPoint']

/** Income kinds the page can filter to. Interest is Phase 2 (renders empty now). */
export type IncomeKind = 'dividend' | 'interest'
export type KindFilter = 'both' | IncomeKind
export type IncomeBasis = 'accrued' | 'received'

/** Which income kinds a section belongs to, so the filter can split them. */
const INTEREST_KINDS = new Set<string>(['interest', 'coupon'])

/**
 * Recurring income for one investor + FY (dividends now; interest later) plus the
 * income-by-FY series that drives the year-over-year chart. The kind filter and the
 * accrued/received basis are client-side view state over the one fetched dataset —
 * each row carries both bases — so switching them never refetches.
 *
 * Fails soft: on error `report` stays null and `error` carries the message.
 */
export function useIncome(investorId: Ref<number>) {
  const fy = ref(currentFy())
  const kind = ref<KindFilter>('both')
  const basis = ref<IncomeBasis>('accrued') // ITR OS default
  const report = ref<IncomeReport | null>(null)
  const byFy = ref<IncomeFyPoint[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const built = ref(false)
  const builtAt = ref<Date | null>(null)
  const integrity = useIntegrityStore()

  async function build(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const rep = await api.GET('/api/investors/{investor_id}/reports/income', {
        params: { path: { investor_id: investorId.value }, query: { fy: fy.value } },
      })
      if (rep.error || !rep.data) throw new Error('income request failed')
      report.value = rep.data
      built.value = true
      builtAt.value = new Date()
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'unknown error'
      report.value = null
    } finally {
      loading.value = false
    }
    // Powers the "not fully reconciled" marker on rows; cached, so cheap.
    void integrity.load(investorId.value)
  }

  /** The year-over-year income chart series — independent of the selected FY, so it
   *  loads once per investor, not on every FY switch. Best-effort: a failure just
   *  empties the chart and leaves the FY report intact. */
  async function loadSeries(): Promise<void> {
    const series = await api.GET('/api/investors/{investor_id}/reports/income-by-fy', {
      params: { path: { investor_id: investorId.value } },
    })
    byFy.value = series && !series.error ? (series.data ?? []) : []
  }

  // Securities with at least one (security, folio) bucket that isn't tax-ready
  // (snapshot / mismatch / incomplete history) — their dividend total may be
  // understated, so the row is flagged.
  const incompleteSecurityIds = computed<Set<number>>(() => {
    const ids = new Set<number>()
    for (const r of integrity.rowsFor(investorId.value)) {
      if (!r.taxSafe) ids.add(r.securityId)
    }
    return ids
  })
  function isIncomplete(securityId: number): boolean {
    return incompleteSecurityIds.value.has(securityId)
  }

  function isInterest(group: IncomeKindGroup): boolean {
    return INTEREST_KINDS.has(group.kind)
  }

  // Groups visible under the current kind filter (a group with no rows is dropped
  // by the backend already, so an empty Interest section simply doesn't appear).
  const visibleGroups = computed<IncomeKindGroup[]>(() => {
    const groups = report.value?.groups ?? []
    if (kind.value === 'both') return groups
    return groups.filter((g) => (kind.value === 'interest' ? isInterest(g) : !isInterest(g)))
  })

  // Row amount on the current basis — dividends read the same either way.
  function rowAmount(row: IncomeRow): number {
    return Number(basis.value === 'received' ? row.received : row.accrued)
  }
  function groupTotal(group: IncomeKindGroup): number {
    return Number(basis.value === 'received' ? group.received_total : group.accrued_total)
  }

  const dividendsTotal = computed(() =>
    (report.value?.groups ?? [])
      .filter((g) => !isInterest(g))
      .reduce((s, g) => s + groupTotal(g), 0),
  )
  const interestTotal = computed(() =>
    (report.value?.groups ?? [])
      .filter((g) => isInterest(g))
      .reduce((s, g) => s + groupTotal(g), 0),
  )
  // Grand total follows the kind filter, so the stat card ties out to what's shown.
  const shownTotal = computed(() => visibleGroups.value.reduce((s, g) => s + groupTotal(g), 0))

  return {
    fy,
    fyOptions: fyOptions(),
    kind,
    basis,
    report,
    byFy,
    loading,
    error,
    built,
    builtAt,
    visibleGroups,
    dividendsTotal,
    interestTotal,
    shownTotal,
    rowAmount,
    groupTotal,
    isInterest,
    isIncomplete,
    currentFy: currentFy(),
    build,
    loadSeries,
  }
}
