import type { DataZoomComponentOption } from 'echarts'

import type { ChartTokens } from './useChartTokens'

/** rgba() from a token hex; passes non-hex through unchanged. */
function rgba(c: string, alpha: number): string {
  if (c.startsWith('#') && (c.length === 7 || c.length === 4)) {
    const hex = c.length === 4 ? c.replace(/#(.)(.)(.)/, '#$1$1$2$2$3$3') : c
    const r = parseInt(hex.slice(1, 3), 16)
    const g = parseInt(hex.slice(3, 5), 16)
    const b = parseInt(hex.slice(5, 7), 16)
    return `rgba(${r},${g},${b},${alpha})`
  }
  return c
}

/** Vertical room (px) the slider + its date labels need under the grid. */
export const DATA_ZOOM_GRID_BOTTOM = 56

/**
 * A time-series zoom control: wheel/drag inside the plot plus a draggable slider
 * (a mini overview of the whole series) pinned at the bottom, themed to the chart
 * tokens. Shared by the portfolio and NAV charts so the interaction is identical.
 */
export function buildDataZoom(t: ChartTokens): DataZoomComponentOption[] {
  return [
    // Drag-to-pan inside the plot, but never grab the mouse wheel — otherwise
    // scrolling the page locks into the chart and zooms instead. Zoom is via the
    // slider handles (and pinch on touch).
    { type: 'inside', throttle: 50, zoomOnMouseWheel: false, moveOnMouseWheel: false },
    {
      type: 'slider',
      height: 26,
      bottom: 6,
      borderColor: t.border,
      backgroundColor: 'transparent',
      fillerColor: rgba(t.verified, 0.12), // the selected window
      handleStyle: { color: t.surface, borderColor: t.muted },
      moveHandleStyle: { color: t.muted },
      emphasis: { handleStyle: { borderColor: t.verified } },
      // The overview line of the full series — the "secondary chart" at the bottom.
      dataBackground: {
        lineStyle: { color: t.border, width: 1 },
        areaStyle: { color: rgba(t.muted, 0.12) },
      },
      selectedDataBackground: {
        lineStyle: { color: t.verified, width: 1 },
        areaStyle: { color: rgba(t.verified, 0.16) },
      },
      textStyle: { color: t.muted, fontSize: 10 },
    },
  ]
}
