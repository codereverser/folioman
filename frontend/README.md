# folioman-frontend

Vue 3 + Pinia + Vite + PrimeVue 4 + ECharts SPA. The same `dist/` bundle is
served by both the desktop PyWebView shell and the hosted Django server, and
ships as a view-only PWA on phones.

## Stack

| Concern | Choice |
|---------|--------|
| Framework | Vue 3 (`<script setup>`, Composition API) |
| Build | Vite 6 |
| Language | TypeScript (strict) |
| State | Pinia |
| Routing | vue-router (history mode) |
| UI kit | **PrimeVue 4** (Aura theme) + PrimeIcons |
| Charts | ECharts (added with the dashboard) |
| PWA | `vite-plugin-pwa` (Workbox) |
| Tests | Vitest + `@vue/test-utils` (jsdom) |
| Node | **24** (pinned via `.nvmrc` + `engines`) |
| Package manager | **pnpm** |

## Scripts

```bash
pnpm install              # install deps (or `make frontend-install` from repo root)
pnpm dev                  # Vite dev server (hello page)
pnpm build                # type-check + production build → dist/
pnpm preview              # serve the production build (exercises the service worker)
pnpm test                 # run Vitest once
pnpm type-check           # vue-tsc strict type-check
pnpm generate-pwa-assets  # regenerate PWA icons from public/logo.svg
```

`VITE_API_BASE` (see `.env.example`) points the SPA at the Django Ninja API;
the typed client is generated against it.

## PWA shell

- `manifest` (name, icons, `display: standalone`, `start_url: /`) and the
  Workbox service worker are configured in [`vite.config.ts`](vite.config.ts).
- App shell (HTML/JS/CSS/icons) is precached; `/api/*` uses **NetworkFirst**
  with a 5s timeout so a flaky mobile network never shows stale portfolio data.
- Icons are generated from a single source SVG (`public/logo.svg`) via
  `@vite-pwa/assets-generator` (`pwa-assets.config.ts`).
- "Add to Home Screen": Android/Chromium use the native prompt
  (`beforeinstallprompt`); iOS Safari gets a one-time Share-sheet tooltip. See
  [`src/composables/usePwaInstall.ts`](src/composables/usePwaInstall.ts).

## Auth token storage

The backend issues JWTs (`django-ninja-jwt`). The client stores them as follows:

- **Access token: in memory only** (a module-level ref / Pinia state). Never in
  `localStorage`/`sessionStorage` — keeps it out of reach of XSS-readable
  storage. It is lost on reload and re-derived from the refresh token.
- **Refresh token:**
  - **Browser / PWA:** `httpOnly`, `Secure`, `SameSite` cookie set by the
    server — not readable from JS. The refresh call rides the cookie.
  - **Future native wrap (Capacitor):** platform secure storage (iOS Keychain /
    Android Keystore) instead of a cookie.

This mirrors the server-side JWT contract. The actual interceptor/refresh wiring
lands with the API client and auth flow, not in this scaffold.
