import { ref, watch, type Ref } from 'vue'
import { api } from '@/api/client'
import type { ValuePoint } from '@/components/charts/PortfolioValueChart.vue'
import { num } from '@/utils/portfolio'

type Pt = { date: string; current: number; invested: number }

// Combine several per-security value series into one, carrying each security's last
// known value forward across dates it has no point for — so a date where one
// security hadn't started yet doesn't dip the class total.
function sumSeries(lists: Pt[][]): ValuePoint[] {
  const dates = [...new Set(lists.flatMap((s) => s.map((p) => p.date)))].sort()
  const ptr = lists.map(() => 0)
  const last = lists.map(() => ({ current: 0, invested: 0 }))
  return dates.map((d) => {
    let current = 0
    let invested = 0
    lists.forEach((s, i) => {
      while (ptr[i] < s.length && s[ptr[i]].date <= d) {
        last[i] = { current: s[ptr[i]].current, invested: s[ptr[i]].invested }
        ptr[i]++
      }
      current += last[i].current
      invested += last[i].invested
    })
    return { date: d, current, invested }
  })
}

/**
 * Value-over-time for one asset class: the per-security value series (each fetched
 * at daily granularity over full history) summed into a single class trend. The
 * chart windows/zooms it client-side, same as the portfolio series.
 */
export function useAssetClassSeries(investorId: Ref<number>, securityIds: Ref<number[]>) {
  const series = ref<ValuePoint[]>([])
  const loading = ref(false)

  async function load(): Promise<void> {
    const ids = securityIds.value
    if (!investorId.value || ids.length === 0) {
      series.value = []
      return
    }
    loading.value = true
    try {
      const results = await Promise.all(
        ids.map((id) =>
          api.GET('/api/investors/{investor_id}/holdings/{security_id}/value-series', {
            params: {
              path: { investor_id: investorId.value, security_id: id },
              query: { granularity: 'daily' },
            },
          }),
        ),
      )
      const lists = results.map(({ data }) =>
        (data?.points ?? []).map((p) => ({
          date: p.date,
          current: num(p.value_inr),
          invested: num(p.invested_inr),
        })),
      )
      series.value = sumSeries(lists)
    } finally {
      loading.value = false
    }
  }

  watch([investorId, securityIds], load, { immediate: true })

  return { series, loading }
}
