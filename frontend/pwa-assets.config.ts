import { defineConfig, minimal2023Preset } from '@vite-pwa/assets-generator/config'

// Generates the PWA icon set (favicon, maskable, apple-touch, 64/192/512 PNGs)
// from a single source SVG. Regenerate with `pnpm generate-pwa-assets`; the
// vite-plugin-pwa `pwaAssets.config` option injects the results into the manifest.
export default defineConfig({
  preset: minimal2023Preset,
  images: ['public/logo.svg'],
})
