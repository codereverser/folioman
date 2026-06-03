import { createApp } from 'vue'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import ConfirmationService from 'primevue/confirmationservice'

// Bundled fonts (privacy: no CDN — assets ship with the desktop build). Loaded
// with `font-display: optional` (see the Vite transform in vite.config.ts) so the
// face never swaps in mid-page: IBM Plex is used when it's ready (the norm once
// cached) and the system fallback is kept for a cold first paint — either way no
// layout shift. The boot skeleton renders in system-ui until the app mounts.
import '@fontsource/ibm-plex-sans/300.css'
import '@fontsource/ibm-plex-sans/400.css'
import '@fontsource/ibm-plex-sans/500.css'
import '@fontsource/ibm-plex-sans/600.css'
import '@fontsource/ibm-plex-sans/700.css'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'

import 'primeicons/primeicons.css'
import './style.css'

import App from './App.vue'
import { router } from './router'
import { FoliomanPreset } from './theme/folioman-preset'
import { registerServiceWorker } from './pwa/registerServiceWorker'

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(PrimeVue, {
  theme: {
    preset: FoliomanPreset,
    options: {
      // Dark theme toggles via a `.dark` class on <html> (driven by the ui store).
      darkModeSelector: '.dark',
      // Keep PrimeVue utility/reset layers below app styles in the cascade.
      cssLayer: { name: 'primevue', order: 'theme, base, primevue' },
    },
  },
})
app.use(ToastService)
app.use(ConfirmationService)

app.mount('#app')

// Register the Workbox service worker (no-op in dev unless devOptions.enabled).
registerServiceWorker()
