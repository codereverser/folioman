import { storeToRefs } from 'pinia'
import { useMetaStore } from '@/stores/meta'

/**
 * Single source of truth for "this instance is read-only" (the hosted demo).
 *
 * Write controls bind their `:disabled` (or a menu item's `disabled`) to
 * `readOnly` so the UI matches what the server allows: every write 403s in demo
 * mode regardless (`DemoReadOnlyMiddleware`), so this is presentation only — it
 * stops a click that would only fail and shows the disabled affordance. The
 * global `DemoBanner` explains why the controls are off.
 */
export function useWriteLock() {
  const { readOnly } = storeToRefs(useMetaStore())
  return { readOnly }
}
