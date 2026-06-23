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

/** A corporate action (split / bonus / merger …) to pin on the NAV line at its
 *  ex-date, so the price moves around it have context. */
export interface NavEvent {
  date: string // ISO ex-date
  label: string // short action label, e.g. "Split"
}

const props = defineProps<{ data: NavPoint[]; markers?: NavMarker[]; events?: NavEvent[] }>()

const tokens = useChartTokens()

const inr = (v: number, max = 4) =>
  `₹${Intl.NumberFormat('en-IN', { maximumFractionDigits: max }).format(v)}`

// NAV on/just before a date — so an event pin sits on the line, not floating.
function navAt(date: string): number | null {
  let val: number | null = null
  for (const p of props.data) {
    if (p.date <= date) val = p.nav
    else break
  }
  return val ?? props.data[0]?.nav ?? null
}

const option = computed<EChartsOption>(() => {
  const buys = (props.markers ?? []).filter((m) => m.type === 'buy').map((m) => [m.date, m.value])
  const sells = (props.markers ?? []).filter((m) => m.type === 'sell').map((m) => [m.date, m.value])
  const events = (props.events ?? [])
    .map((e) => ({ date: e.date, nav: navAt(e.date), label: e.label }))
    .filter((e): e is { date: string; nav: number; label: string } => e.nav != null)
    .map((e) => ({ value: [e.date, e.nav] as [string, number], name: e.label }))
  const hasOverlay = !!(props.markers?.length || events.length)
  return {
    tooltip: {
      trigger: 'axis',
      backgroundColor: tokens.value.surface,
      borderColor: tokens.value.border,
      textStyle: { color: tokens.value.text },
      valueFormatter: (v) => (v == null ? '—' : inr(v as number)),
    },
    legend: hasOverlay
      ? {
          data: [
            ...(props.markers?.length ? ['Buy', 'Sell'] : []),
            ...(events.length ? ['Action'] : []),
          ],
          right: 8,
          top: 0,
          textStyle: { color: tokens.value.muted },
          icon: 'circle',
        }
      : undefined,
    grid: {
      left: 8,
      right: 8,
      top: hasOverlay ? 28 : 16,
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
        // Neutral grey line so the green Buy / red Sell markers read clearly against
        // it (the brand green clashed with the buy triangles).
        lineStyle: { width: 1.5, color: tokens.value.muted },
        itemStyle: { color: tokens.value.muted },
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
      {
        name: 'Action',
        type: 'scatter',
        // Neutral pin dropped on the line at each corporate action's ex-date; the
        // action's name shows in the tooltip and as a small caption above the pin.
        symbol: 'pin',
        symbolSize: 22,
        symbolOffset: [0, '-50%'],
        itemStyle: { color: tokens.value.muted },
        label: {
          show: true,
          position: 'top',
          formatter: (p) => (p as { name: string }).name,
          color: tokens.value.muted,
          fontSize: 10,
        },
        tooltip: {
          trigger: 'item',
          formatter: (p) => {
            const e = p as unknown as { name: string; value: [string, number] }
            return `${e.name} · ${inr(e.value[1])}`
          },
        },
        data: events,
        z: 4,
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
