<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import type { EChartsOption } from 'echarts'
import '@/charts/echarts' // registers the tree-shaken ECharts modules (side-effect)
import { useChartTokens } from '@/charts/useChartTokens'
import { formatInrCompact, formatInr } from '@/utils/format'

/** One stacked series (e.g. Dividends, or LTCG) — its key indexes each point's values. */
export interface FyBarSeries {
  key: string
  label: string
  color: string
}

/** One financial year's bar: the stacked values keyed by series. */
export interface FyBarPoint {
  fy: string
  values: Record<string, number>
}

const props = defineProps<{
  points: FyBarPoint[]
  series: FyBarSeries[]
  /** Rendered lighter and flagged as partial — the year isn't over yet. */
  currentFy?: string
}>()

// Clicking a year bubbles the FY up so a page can focus that year's detail.
const emit = defineEmits<{ (e: 'select', fy: string): void }>()

const tokens = useChartTokens()

const hasNegative = computed(() =>
  props.points.some((p) => props.series.some((s) => (p.values[s.key] ?? 0) < 0)),
)

const option = computed<EChartsOption>(() => {
  const categories = props.points.map((p) => p.fy)
  const barSeries = props.series.map((s) => ({
    name: s.label,
    type: 'bar' as const,
    stack: 'total',
    itemStyle: { color: s.color },
    emphasis: { focus: 'series' as const },
    data: props.points.map((p) => {
      const value = p.values[s.key] ?? 0
      // Dim the in-progress current-FY bar so it reads as not-yet-final.
      return p.fy === props.currentFy ? { value, itemStyle: { opacity: 0.45 } } : value
    }),
  }))

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: tokens.value.surface,
      borderColor: tokens.value.border,
      textStyle: { color: tokens.value.text },
      valueFormatter: (v) => (v == null ? '—' : formatInr(v as number)),
    },
    legend: {
      data: props.series.map((s) => s.label),
      top: 0,
      right: 8,
      textStyle: { color: tokens.value.muted },
      icon: 'roundRect',
    },
    grid: { left: 8, right: 8, top: 32, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: categories,
      axisLine: { lineStyle: { color: tokens.value.border } },
      axisTick: { show: false },
      axisLabel: { color: tokens.value.muted },
    },
    yAxis: {
      type: 'value',
      // A zero baseline so net-loss years drop below it visibly.
      splitLine: { lineStyle: { color: tokens.value.border } },
      axisLabel: {
        color: tokens.value.muted,
        formatter: (v: number) => formatInrCompact(v),
      },
    },
    series: barSeries,
  }
})

function onClick(params: unknown): void {
  const name = (params as { name?: string }).name
  if (name) emit('select', name)
}
</script>

<template>
  <div class="fy-bar-wrap">
    <VChart class="fy-bar" :option="option" autoresize @click="onClick" />
    <p v-if="currentFy" class="fy-bar-note">
      <span class="partial-swatch" aria-hidden="true" />
      {{ currentFy }} is the current year — still in progress.
      <template v-if="hasNegative"> Bars below the line are net losses.</template>
    </p>
  </div>
</template>

<style scoped>
.fy-bar-wrap {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.fy-bar {
  height: 260px;
  width: 100%;
}
.fy-bar-note {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.75rem;
  color: var(--fm-text-subtle);
}
.partial-swatch {
  display: inline-block;
  width: 0.7rem;
  height: 0.7rem;
  border-radius: 2px;
  background: var(--fm-text-muted);
  opacity: 0.45;
}
</style>
