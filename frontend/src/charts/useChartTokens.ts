import { ref, watch } from 'vue'
import { useUiStore } from '@/stores/ui'

/**
 * ECharts renders to canvas and can't read CSS custom properties, so we resolve
 * the design tokens to concrete colour strings and re-read them whenever the
 * theme flips. Single source of truth stays in tokens.css.
 */
export interface ChartTokens {
  text: string
  muted: string
  border: string
  surface: string
  verified: string
  gain: string
  loss: string
  assetPalette: string[]
}

function readVar(name: string): string {
  if (typeof document === 'undefined') return ''
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function readTokens(): ChartTokens {
  return {
    text: readVar('--fm-text'),
    muted: readVar('--fm-text-muted'),
    border: readVar('--fm-border-subtle'),
    surface: readVar('--fm-surface-overlay'),
    verified: readVar('--fm-verified'),
    gain: readVar('--fm-gain'),
    loss: readVar('--fm-loss'),
    assetPalette: [
      readVar('--fm-asset-equity'),
      readVar('--fm-asset-debt'),
      readVar('--fm-asset-gold'),
      readVar('--fm-asset-realestate'),
      readVar('--fm-asset-crypto'),
      readVar('--fm-asset-intl'),
      readVar('--fm-asset-cash'),
    ],
  }
}

export function useChartTokens() {
  const ui = useUiStore()
  const tokens = ref<ChartTokens>(readTokens())
  // Re-resolve after the .dark class settles on the next frame.
  watch(
    () => ui.isDark,
    () => {
      requestAnimationFrame(() => {
        tokens.value = readTokens()
      })
    },
  )
  return tokens
}
