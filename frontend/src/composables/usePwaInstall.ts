import { onBeforeUnmount, onMounted, readonly, ref } from 'vue'

/**
 * The `beforeinstallprompt` event isn't in the standard lib DOM types yet.
 * https://developer.mozilla.org/en-US/docs/Web/API/BeforeInstallPromptEvent
 */
interface BeforeInstallPromptEvent extends Event {
  readonly platforms: string[]
  prompt(): Promise<void>
  readonly userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>
}

/**
 * "Add to Home Screen" UX.
 *
 * - Android/Chromium fire `beforeinstallprompt`; we stash the event and expose
 *   {@link promptInstall} to trigger the native prompt from a user gesture.
 * - iOS Safari has no such event, so {@link isIos} drives a one-time tooltip
 *   telling the user to use the Share → "Add to Home Screen" flow manually.
 * - {@link isStandalone} suppresses both once the app is already installed.
 */
export function usePwaInstall() {
  const deferredPrompt = ref<BeforeInstallPromptEvent | null>(null)
  const canInstall = ref(false)
  const installed = ref(false)

  const isStandalone =
    typeof window !== 'undefined' &&
    (window.matchMedia('(display-mode: standalone)').matches ||
      // iOS Safari exposes navigator.standalone instead of display-mode.
      (window.navigator as Navigator & { standalone?: boolean }).standalone === true)

  const isIos =
    typeof window !== 'undefined' &&
    /iphone|ipad|ipod/i.test(window.navigator.userAgent) &&
    !/crios|fxios/i.test(window.navigator.userAgent)

  function onBeforeInstallPrompt(event: Event): void {
    // Stop Chrome's mini-infobar; we drive the prompt ourselves.
    event.preventDefault()
    deferredPrompt.value = event as BeforeInstallPromptEvent
    canInstall.value = true
  }

  function onAppInstalled(): void {
    installed.value = true
    canInstall.value = false
    deferredPrompt.value = null
  }

  async function promptInstall(): Promise<boolean> {
    const event = deferredPrompt.value
    if (!event) return false
    await event.prompt()
    const choice = await event.userChoice
    deferredPrompt.value = null
    canInstall.value = false
    return choice.outcome === 'accepted'
  }

  onMounted(() => {
    window.addEventListener('beforeinstallprompt', onBeforeInstallPrompt)
    window.addEventListener('appinstalled', onAppInstalled)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('beforeinstallprompt', onBeforeInstallPrompt)
    window.removeEventListener('appinstalled', onAppInstalled)
  })

  return {
    canInstall: readonly(canInstall),
    installed: readonly(installed),
    isIos,
    isStandalone,
    promptInstall,
  }
}
