<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import type { EChartsOption } from 'echarts'
import '@/charts/echarts' // registers the tree-shaken ECharts modules (side-effect)
import { useChartTokens } from '@/charts/useChartTokens'

export interface NavPoint {
  date: string // ISO date
  nav: number
}

const props = defineProps<{ data: NavPoint[] }>()

const tokens = useChartTokens()

const option = computed<EChartsOption>(() => ({
  tooltip: {
    trigger: 'axis',
    backgroundColor: tokens.value.surface,
    borderColor: tokens.value.border,
    textStyle: { color: tokens.value.text },
    valueFormatter: (v) => `₹${Intl.NumberFormat('en-IN', { maximumFractionDigits: 4 }).format(v as number)}`,
  },
  grid: { left: 8, right: 8, top: 16, bottom: 8, containLabel: true },
  xAxis: {
    type: 'category',
    boundaryGap: false,
    data: props.data.map((d) => d.date),
    axisLine: { lineStyle: { color: tokens.value.border } },
    axisLabel: { color: tokens.value.muted, hideOverlap: true },
  },
  yAxis: {
    type: 'value',
    scale: true, // NAV rarely starts at 0; don't waste vertical space
    splitLine: { lineStyle: { color: tokens.value.border } },
    axisLabel: {
      color: tokens.value.muted,
      formatter: (v: number) => `₹${Intl.NumberFormat('en-IN', { notation: 'compact' }).format(v)}`,
    },
  },
  series: [
    {
      name: 'NAV',
      type: 'line',
      smooth: true,
      symbol: 'none',
      lineStyle: { width: 2, color: tokens.value.verified },
      itemStyle: { color: tokens.value.verified },
      data: props.data.map((d) => d.nav),
    },
  ],
}))
</script>

<template>
  <VChart class="nav-chart" :option="option" autoresize />
</template>

<style scoped>
.nav-chart {
  height: 260px;
  width: 100%;
}
</style>
