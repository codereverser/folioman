import { ref } from 'vue'

/**
 * Shared "a new build is waiting" signal, set by the service-worker registration
 * and read by the update banner. Kept in a tiny module (not a Pinia store) so the
 * SW registration — which runs at boot, outside any component — can flip it.
 */
export const updateAvailable = ref(false)

let updater: (() => Promise<void>) | null = null

/** Wire the SW's activate-and-reload action (called by registerServiceWorker). */
export function setUpdater(fn: () => Promise<void>): void {
  updater = fn
}

/**
 * Apply the pending update: activate the waiting service worker and reload onto
 * the new build. Falls back to a plain reload where there is no SW (the desktop
 * shell, or dev) — that still re-fetches the current bundle.
 */
export async function applyUpdate(): Promise<void> {
  if (updater) await updater()
  else window.location.reload()
}
