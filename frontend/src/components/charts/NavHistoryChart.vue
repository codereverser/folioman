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

/** A transaction to overlay on the NAV line, so you can see what you paid (or
 *  received) against the price at the time. */
export interface NavMarker {
  date: string // ISO date
  value: number // NAV / price at the transaction
  type: 'buy' | 'sell'
}

const props = defineProps<{ data: NavPoint[]; markers?: NavMarker[] }>()

const tokens = useChartTokens()

const inr = (v: number, max = 4) =>
  `₹${Intl.NumberFormat('en-IN', { maximumFractionDigits: max }).format(v)}`

const option = computed<EChartsOption>(() => {
  const buys = (props.markers ?? []).filter((m) => m.type === 'buy').map((m) => [m.date, m.value])
  const sells = (props.markers ?? []).filter((m) => m.type === 'sell').map((m) => [m.date, m.value])
  return {
    tooltip: {
      trigger: 'axis',
      backgroundColor: tokens.value.surface,
      borderColor: tokens.value.border,
      textStyle: { color: tokens.value.text },
      valueFormatter: (v) => (v == null ? '—' : inr(v as number)),
    },
    legend: props.markers?.length
      ? {
          data: ['Buy', 'Sell'],
          right: 8,
          top: 0,
          textStyle: { color: tokens.value.muted },
          icon: 'circle',
        }
      : undefined,
    grid: {
      left: 8,
      right: 8,
      top: props.markers?.length ? 28 : 16,
      bottom: 8,
      containLabel: true,
    },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: tokens.value.border } },
      axisLabel: { color: tokens.value.muted, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      scale: true, // NAV rarely starts at 0; don't waste vertical space
      splitLine: { lineStyle: { color: tokens.value.border } },
      axisLabel: {
        color: tokens.value.muted,
        formatter: (v: number) =>
          `₹${Intl.NumberFormat('en-IN', { notation: 'compact' }).format(v)}`,
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
        data: props.data.map((d) => [d.date, d.nav]),
        z: 1,
      },
      {
        name: 'Buy',
        type: 'scatter',
        symbol: 'triangle',
        symbolSize: 9,
        itemStyle: { color: tokens.value.gain },
        data: buys,
        z: 3,
      },
      {
        name: 'Sell',
        type: 'scatter',
        symbol: 'triangle',
        symbolRotate: 180,
        symbolSize: 9,
        itemStyle: { color: tokens.value.loss },
        data: sells,
        z: 3,
      },
    ],
  }
})
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
