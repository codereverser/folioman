import { fileURLToPath, URL } from 'node:url'

import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'
import { compression } from 'vite-plugin-compression2'

// @fontsource ships `font-display: swap`, which would let the bundled IBM Plex
// swap in over the system fallback after the app mounts — reflowing text (CLS).
// `optional` instead keeps whatever font is ready at first paint for the page's
// lifetime: IBM Plex once cached, the system fallback on a cold first load — never
// a mid-page swap. Rewritten at build time so we don't hand-author every subset.
const fontDisplayOptional: Plugin = {
  name: 'font-display-optional',
  enforce: 'pre',
  transform(code, id) {
    if (id.includes('@fontsource') && id.endsWith('.css') && code.includes('font-display')) {
      return { code: code.replace(/font-display:\s*swap/g, 'font-display:optional'), map: null }
    }
    return null
  },
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    fontDisplayOptional,
    vue(),
    VitePWA({
      // Auto-apply new service workers; the app is view-only on mobile so a
      // silent refresh of the shell is fine. The "Add to Home Screen" prompt
      // (beforeinstallprompt / iOS tooltip) is handled separately in the UI.
      registerType: 'autoUpdate',
      // Icons + apple-touch links are generated from public/logo.svg and
      // injected into the manifest by the assets generator (pwa-assets.config.ts).
      pwaAssets: { config: true },
      manifest: {
        name: 'Folioman',
        short_name: 'Folioman',
        description: 'Mutual-fund portfolio tracking for advisors and families.',
        theme_color: '#0B1120',
        background_color: '#ffffff',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        orientation: 'portrait',
      },
      workbox: {
        // Precache the app shell (HTML + JS + CSS + icons).
        globPatterns: ['**/*.{js,css,html,svg,png,ico,woff2}'],
        // SPA deep links fall back to index.html; never intercept the API.
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [
          {
            // A flaky mobile network should never show stale portfolio data:
            // hit the network first, fall back to the last cached response.
            urlPattern: ({ url }) => url.pathname.startsWith('/api/'),
            handler: 'NetworkFirst',
            options: {
              cacheName: 'folioman-api',
              networkTimeoutSeconds: 5,
              expiration: { maxEntries: 64, maxAgeSeconds: 60 * 60 * 24 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
      devOptions: {
        // Allow exercising the SW during `pnpm dev` without a production build.
        enabled: false,
      },
    }),
    // Emit .br + .gz beside each asset so WhiteNoise (which only *serves*
    // precompressed files, it doesn't compress on the fly) ships the bundle
    // compressed — the single biggest Lighthouse win (~1 MB → ~250 KB). Runs
    // after VitePWA, and .br/.gz fall outside the SW globPatterns so they aren't
    // double-precached. threshold skips files too small to be worth compressing.
    compression({ algorithms: ['gzip', 'brotliCompress'], threshold: 1024 }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    // Two chunks exceed Vite's 500 kB default, both intentionally:
    //  - the ECharts chunk is dynamically imported (the donut/charts lazy-load only
    //    when a view that renders one mounts), so it never weighs on first paint;
    //  - the entry chunk carries Vue + PrimeVue (incl. the DataTable, which must load
    //    eagerly) for the landing dashboard.
    // Both ship precompressed (.br/.gz, see the compression plugin) at ~190–220 kB,
    // and the primary target is a local desktop binary where transfer size is moot.
    // So this raises the advisory threshold rather than forcing fragile manual chunks.
    chunkSizeWarningLimit: 1000,
  },
  // Dev: the SPA is served by Vite and proxies /api to the Django dev server, so
  // the browser sees one origin (no CORS) — matching production, where Django
  // serves both. Override the API target with VITE_DEV_API_TARGET if needed.
  server: {
    proxy: {
      '/api': process.env.VITE_DEV_API_TARGET ?? 'http://localhost:8000',
    },
  },
})
