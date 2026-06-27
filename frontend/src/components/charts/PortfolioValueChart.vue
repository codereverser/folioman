<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import type { EChartsOption } from 'echarts'
import '@/charts/echarts' // registers the tree-shaken ECharts modules (side-effect)
import { useChartTokens } from '@/charts/useChartTokens'
import { buildDataZoom, DATA_ZOOM_GRID_BOTTOM } from '@/charts/dataZoom'
import { formatInr, formatDate, formatDayMonth, formatMonthYear } from '@/utils/format'

export interface ValuePoint {
  date: string // ISO date
  current: number
  invested: number
}

const props = withDefaults(
  defineProps<{
    data: ValuePoint[]
    granularity?: 'daily' | 'weekly' | 'monthly'
    // Show the draggable zoom slider (a mini overview to focus any window). On by
    // default; callers in tight cards can turn it off.
    zoomable?: boolean
    // Initial zoom window [from, to] (ISO dates). The slider still shows the full
    // series; this just sets which portion is in view (e.g. a "1Y" preset). null =
    // show the whole range.
    window?: { from: string; to: string } | null
  }>(),
  { granularity: 'monthly', zoomable: true, window: null },
)

const tokens = useChartTokens()

// Translate the [from, to] window into dataZoom start/end percentages over the
// (full) series, so the main plot opens on that slice while the slider overview
// still spans everything. A free drag on the slider then takes over from here.
const zoomBounds = computed<{ start: number; end: number }>(() => {
  const w = props.window
  const n = props.data.length
  if (!w || n === 0) return { start: 0, end: 100 }
  const span = n - 1 || 1
  const firstAtOrAfter = props.data.findIndex((d) => d.date >= w.from)
  const start = firstAtOrAfter < 0 ? 0 : (firstAtOrAfter / span) * 100
  let lastAtOrBefore = -1
  for (let i = 0; i < n; i++) if (props.data[i].date <= w.to) lastAtOrBefore = i
  const end = lastAtOrBefore < 0 ? 100 : (lastAtOrBefore / span) * 100
  return { start, end: Math.max(end, start) }
})

// Axis ticks match the sampling: day-level windows read "30 May", multi-year
// monthly windows read "May 2025". hideOverlap thins whatever doesn't fit.
const axisLabel = computed(() =>
  props.granularity === 'monthly' ? formatMonthYear : formatDayMonth,
)

const option = computed<EChartsOption>(() => ({
  tooltip: {
    trigger: 'axis',
    backgroundColor: tokens.value.surface,
    borderColor: tokens.value.border,
    textStyle: { color: tokens.value.text },
    // Full date header (the axis shows month-year; hover reveals the exact day)
    // with exact rupees for inspection.
    formatter: (params) => {
      const rows = params as unknown as {
        axisValue: string
        marker: string
        seriesName: string
        value: number
      }[]
      if (!rows.length) return ''
      const header = formatDate(rows[0].axisValue)
      const body = rows
        .map((r) => `${r.marker} ${r.seriesName}: <b>${formatInr(r.value)}</b>`)
        .join('<br/>')
      return `<div style="margin-bottom:4px">${header}</div>${body}`
    },
  },
  legend: {
    top: 0,
    right: 0,
    icon: 'roundRect',
    textStyle: { color: tokens.value.muted },
    data: ['Current value', 'Invested'],
  },
  grid: {
    left: 8,
    right: 8,
    top: 36,
    bottom: props.zoomable ? DATA_ZOOM_GRID_BOTTOM : 8,
    containLabel: true,
  },
  dataZoom: props.zoomable
    ? buildDataZoom(tokens.value).map((z) => ({ ...z, ...zoomBounds.value }))
    : undefined,
  xAxis: {
    type: 'category',
    boundaryGap: false,
    data: props.data.map((d) => d.date),
    axisLine: { lineStyle: { color: tokens.value.border } },
    axisLabel: {
      color: tokens.value.muted,
      hideOverlap: true,
      formatter: (val: string) => axisLabel.value(val),
    },
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
      // Down-sample the daily series for render: a smoothed line when zoomed out,
      // finer detail as the zoom window narrows.
      sampling: 'lttb',
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
      sampling: 'lttb',
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
  <VChart class="value-chart" :class="{ 'is-zoomable': zoomable }" :option="option" autoresize />
</template>

<style scoped>
.value-chart {
  height: 280px;
  width: 100%;
}
/* Taller when the zoom slider is on, so the plot isn't squeezed by it. */
.value-chart.is-zoomable {
  height: 320px;
}
</style>
