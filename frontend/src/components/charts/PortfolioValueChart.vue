<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import type { EChartsOption } from 'echarts'
import '@/charts/echarts' // registers the tree-shaken ECharts modules (side-effect)
import { useChartTokens } from '@/charts/useChartTokens'
import { formatInr } from '@/utils/format'

export interface ValuePoint {
  date: string // ISO date
  current: number
  invested: number
}

const props = defineProps<{ data: ValuePoint[] }>()

const tokens = useChartTokens()

const option = computed<EChartsOption>(() => ({
  tooltip: {
    trigger: 'axis',
    backgroundColor: tokens.value.surface,
    borderColor: tokens.value.border,
    textStyle: { color: tokens.value.text },
    valueFormatter: (v) => formatInr(v as number),
  },
  legend: {
    top: 0,
    right: 0,
    icon: 'roundRect',
    textStyle: { color: tokens.value.muted },
    data: ['Current value', 'Invested'],
  },
  grid: { left: 8, right: 8, top: 36, bottom: 8, containLabel: true },
  xAxis: {
    type: 'category',
    boundaryGap: false,
    data: props.data.map((d) => d.date),
    axisLine: { lineStyle: { color: tokens.value.border } },
    axisLabel: { color: tokens.value.muted, hideOverlap: true },
  },
  yAxis: {
    type: 'value',
    splitLine: { lineStyle: { color: tokens.value.border } },
    axisLabel: {
      color: tokens.value.muted,
      formatter: (v: number) => `₹${Intl.NumberFormat('en-IN', { notation: 'compact' }).format(v)}`,
    },
  },
  series: [
    {
      name: 'Current value',
      type: 'line',
      smooth: true,
      symbol: 'none',
      lineStyle: { width: 2, color: tokens.value.verified },
      itemStyle: { color: tokens.value.verified },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            { offset: 0, color: color(tokens.value.verified, 0.22) },
            { offset: 1, color: color(tokens.value.verified, 0) },
          ],
        },
      },
      data: props.data.map((d) => d.current),
    },
    {
      name: 'Invested',
      type: 'line',
      smooth: true,
      symbol: 'none',
      lineStyle: { width: 1.5, type: 'dashed', color: tokens.value.muted },
      itemStyle: { color: tokens.value.muted },
      data: props.data.map((d) => d.invested),
    },
  ],
}))

// Build an rgba from a token colour string for the area gradient.
function color(c: string, alpha: number): string {
  if (c.startsWith('#') && (c.length === 7 || c.length === 4)) {
    const hex = c.length === 4 ? c.replace(/#(.)(.)(.)/, '#$1$1$2$2$3$3') : c
    const r = parseInt(hex.slice(1, 3), 16)
    const g = parseInt(hex.slice(3, 5), 16)
    const b = parseInt(hex.slice(5, 7), 16)
    return `rgba(${r},${g},${b},${alpha})`
  }
  return c
}
</script>

<template>
  <VChart class="value-chart" :option="option" autoresize />
</template>

<style scoped>
.value-chart {
  height: 280px;
  width: 100%;
}
</style>
