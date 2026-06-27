<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import type { EChartsOption } from 'echarts'
import '@/charts/echarts' // registers the tree-shaken ECharts modules (side-effect)
import { useChartTokens } from '@/charts/useChartTokens'
import { formatInr } from '@/utils/format'

export interface AllocationSlice {
  name: string
  value: number
  /** Optional explicit colour (e.g. fixed asset-class colour). */
  color?: string
}

const props = defineProps<{
  data: AllocationSlice[]
  /** Center label, e.g. total net worth. */
  centerLabel?: string
  /** Hide the built-in legend (when the caller renders its own, e.g. a side list). */
  hideLegend?: boolean
}>()

const emit = defineEmits<{ (e: 'slice', name: string): void }>()

const tokens = useChartTokens()

// ECharts paints to canvas and can't read CSS vars — resolve `var(--x)` slice
// colours to concrete values. Reads `tokens.value` so it re-resolves on theme flip.
function resolveColor(raw: string | undefined, i: number): string {
  const fallback = tokens.value.assetPalette[i % tokens.value.assetPalette.length]
  if (!raw) return fallback
  if (raw.startsWith('var(') && typeof document !== 'undefined') {
    const name = raw.slice(4, -1).trim()
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
  }
  return raw
}

const option = computed<EChartsOption>(() => ({
  // Colour is set per slice via itemStyle (below), not as a top-level `color`
  // palette: vue-echarts builds its `replaceMerge` list from the option's
  // top-level keys when the option object is replaced, and `color` isn't a valid
  // component type — it throws "color is not valid component main type" and the
  // chart freezes on the next update (e.g. the allocation grouping toggle).
  tooltip: {
    trigger: 'item',
    backgroundColor: tokens.value.surface,
    borderColor: tokens.value.border,
    textStyle: { color: tokens.value.text },
    valueFormatter: (v) => formatInr(v as number),
  },
  legend: props.hideLegend
    ? undefined
    : {
        bottom: 0,
        // One scrollable row so a long tail of slices (e.g. many AMCs) never wraps up
        // over the ring / center label; ‹ › page through the rest.
        type: 'scroll',
        icon: 'circle',
        textStyle: { color: tokens.value.muted },
        pageIconColor: tokens.value.muted,
        pageIconInactiveColor: tokens.value.border,
        pageTextStyle: { color: tokens.value.muted },
      },
  series: [
    {
      type: 'pie',
      radius: ['58%', '82%'],
      center: ['50%', props.hideLegend ? '50%' : '46%'],
      padAngle: 2,
      itemStyle: { borderRadius: 4, borderColor: tokens.value.surface, borderWidth: 2 },
      label: { show: false },
      emphasis: {
        label: { show: true, fontSize: 14, fontWeight: 'bold', color: tokens.value.text },
        scaleSize: 6,
      },
      data: props.data.map((d, i) => ({
        name: d.name,
        value: d.value,
        itemStyle: { color: resolveColor(d.color, i) },
      })),
    },
  ],
}))

function onClick(params: { name?: string }): void {
  if (params.name) emit('slice', params.name)
}
</script>

<template>
  <div class="donut-wrap">
    <VChart class="donut" :option="option" autoresize @click="onClick" />
    <div v-if="centerLabel" class="center" :class="{ centered: hideLegend }" aria-hidden="true">
      <span class="center-label">Total</span>
      <span class="center-value">{{ centerLabel }}</span>
    </div>
  </div>
</template>

<style scoped>
.donut-wrap {
  position: relative;
  height: 260px;
}
.donut {
  height: 100%;
  width: 100%;
}
.center {
  position: absolute;
  top: 44%;
  left: 50%;
  transform: translate(-50%, -50%);
}
.center.centered {
  top: 50%;
  display: flex;
  flex-direction: column;
  align-items: center;
  pointer-events: none;
}
.center-label {
  font-size: 0.6875rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--fm-text-muted);
}
.center-value {
  font-size: 1.125rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--fm-text);
}
</style>
