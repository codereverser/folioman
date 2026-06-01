import { computed, type Ref } from 'vue'
import type { AllocationSlice } from '@/components/charts/AllocationDonut.vue'
import type { ValuePoint } from '@/components/charts/PortfolioValueChart.vue'
import { rollupIntegrity, type IntegrityRollup, type IntegrityStatus } from '@/integrity/status'

export interface HoldingRow {
  securityId: number
  name: string
  assetClass: string
  value: number
  units: number
  returnPct: number
  integrity: IntegrityStatus
}

export interface DashboardSummary {
  netWorth: number
  invested: number
  dayChangeAmount: number
  dayChangePercent: number
  totalReturnAmount: number
  totalReturnPercent: number
  xirr: number
  asOf: string
  allocation: AllocationSlice[]
  valueSeries: ValuePoint[]
  topHoldings: HoldingRow[]
}

// Fixed asset-class → CSS-var colour so slices stay semantic across the app.
const ASSET_COLORS: Record<string, string> = {
  Equity: 'var(--fm-asset-equity)',
  Debt: 'var(--fm-asset-debt)',
  Gold: 'var(--fm-asset-gold)',
  Cash: 'var(--fm-asset-cash)',
  'Real Estate': 'var(--fm-asset-realestate)',
  Crypto: 'var(--fm-asset-crypto)',
  International: 'var(--fm-asset-intl)',
}

function seedSeries(): ValuePoint[] {
  // 13 monthly points, gentle growth with a mid dip — representative shape.
  const months = ['Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr']
  const current = [51, 52.4, 50.1, 54, 57.2, 55.6, 59, 61.5, 63.1, 66, 64.2, 68.5, 70.1]
  const invested = [49, 49.6, 50, 50.5, 51, 51.4, 51.9, 52.3, 52.8, 53.2, 53.5, 53.9, 54.2]
  return months.map((m, i) => ({
    date: m,
    current: Math.round(current[i] * 100000),
    invested: Math.round(invested[i] * 100000),
  }))
}

/**
 * Dashboard summary for an investor.
 *
 * NOTE: seeded with representative placeholder data — there is no per-investor
 * summary endpoint yet (only /families/{id}/aggregate). When the backend adds
 * one, replace the body of `buildSeed` with a typed `api.GET(...)` call; the
 * returned shape and every consumer stay the same.
 */
export function useDashboard(_investorId: Ref<number>) {
  const summary = computed<DashboardSummary>(() => buildSeed())

  const seedHoldings: HoldingRow[] = summary.value.topHoldings
  const seedIntegrityRollup: IntegrityRollup = rollupIntegrity(
    seedHoldings.map((h) => h.integrity),
  )

  return { summary, seedIntegrityRollup }
}

function buildSeed(): DashboardSummary {
  const topHoldings: HoldingRow[] = [
    { securityId: 1, name: 'Parag Parikh Flexi Cap — Direct Growth', assetClass: 'Equity', value: 1264300, units: 18234.51, returnPct: 41.2, integrity: 'full_history' },
    { securityId: 2, name: 'Axis Long Term Equity — Direct Growth', assetClass: 'Equity', value: 336800, units: 4974.48, returnPct: 43.93, integrity: 'full_history' },
    { securityId: 3, name: 'HDFC Corporate Bond — Direct Growth', assetClass: 'Debt', value: 502400, units: 4825.07, returnPct: 7.4, integrity: 'reconciled' },
    { securityId: 4, name: 'SBI Gold ETF', assetClass: 'Gold', value: 218900, units: 312.0, returnPct: 18.6, integrity: 'snapshot_only' },
    { securityId: 5, name: 'RELIANCE', assetClass: 'Equity', value: 184500, units: 64.0, returnPct: -3.1, integrity: 'snapshot_only' },
    { securityId: 6, name: 'TCS', assetClass: 'Equity', value: 142600, units: 38.0, returnPct: 12.4, integrity: 'mismatch' },
  ]
  const allocation: AllocationSlice[] = [
    { name: 'Equity', value: 4350000, color: ASSET_COLORS.Equity },
    { name: 'Debt', value: 1470000, color: ASSET_COLORS.Debt },
    { name: 'Gold', value: 560000, color: ASSET_COLORS.Gold },
    { name: 'Cash', value: 320000, color: ASSET_COLORS.Cash },
    { name: 'Crypto', value: 312640, color: ASSET_COLORS.Crypto },
  ]
  return {
    netWorth: 7012640,
    invested: 5420000,
    dayChangeAmount: 4700,
    dayChangePercent: 0.07,
    totalReturnAmount: 1592640,
    totalReturnPercent: 29.38,
    xirr: 18.49,
    asOf: 'as of today',
    allocation,
    valueSeries: seedSeries(),
    topHoldings,
  }
}
