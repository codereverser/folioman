import { computed, ref } from 'vue'
import { RANGES, type RangeKey } from '@/utils/portfolio'

export type { RangeKey }

/**
 * The client-side range presets shared by every dashboard chart: the value
 * series is fetched once over full history, and the toggle just windows it —
 * switching ranges needs no network. `valueWindow` is the chart's zoom bounds
 * (null = All, no window); `granularity` drives the axis tick density.
 */
export function useRangeWindow(initial: RangeKey = '1Y') {
  const range = ref<RangeKey>(initial)

  function setRange(next: RangeKey): void {
    range.value = next
  }

  const valueWindow = computed(() =>
    range.value === 'All'
      ? null
      : { from: RANGES[range.value].from(), to: new Date().toISOString().slice(0, 10) },
  )

  const granularity = computed(() => RANGES[range.value].granularity)

  return { range, setRange, valueWindow, granularity }
}
