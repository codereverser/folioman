import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
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
        theme_color: '#4f46e5',
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
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
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
