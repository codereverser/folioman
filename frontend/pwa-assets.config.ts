import { defineConfig, minimal2023Preset } from '@vite-pwa/assets-generator/config'

// Generates the PWA icon set (favicon, maskable, apple-touch, 64/192/512 PNGs)
// from a single source SVG. Regenerate with `pnpm generate-pwa-assets`; the
// vite-plugin-pwa `pwaAssets.config` option injects the results into the manifest.
//
// The mark sits on a dark navy tile, so the maskable + apple-touch safe-zone
// padding is filled with that same navy (instead of the preset's transparent /
// white) — otherwise the OS mask shows the tile floating on a transparent or
// white field.
const NAVY = '#0B1120'

export default defineConfig({
  preset: {
    ...minimal2023Preset,
    maskable: {
      ...minimal2023Preset.maskable,
      resizeOptions: { ...minimal2023Preset.maskable.resizeOptions, background: NAVY },
    },
    apple: {
      ...minimal2023Preset.apple,
      resizeOptions: { ...minimal2023Preset.apple.resizeOptions, background: NAVY },
    },
  },
  images: ['public/logo.svg'],
})
