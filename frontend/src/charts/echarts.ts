/**
 * ECharts registration — tree-shaken core build. Imported once for its side
 * effect (see main.ts) so <VChart> components only pull the pieces we use.
 */
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { PieChart, LineChart, ScatterChart, BarChart } from 'echarts/charts'
import {
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DatasetComponent,
  DataZoomComponent,
} from 'echarts/components'

use([
  CanvasRenderer,
  PieChart,
  LineChart,
  ScatterChart,
  BarChart,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DatasetComponent,
  DataZoomComponent,
])
