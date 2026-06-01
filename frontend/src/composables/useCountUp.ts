import { onMounted, ref, watch, type Ref } from 'vue'

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

/**
 * Animate a number from 0 → target on first paint (and on subsequent target
 * changes). Snaps instantly when the user prefers reduced motion. Returns a
 * reactive number the caller formats however it likes.
 */
export function useCountUp(target: Ref<number>, durationMs = 600): Ref<number> {
  const display = ref(0)

  function run(to: number): void {
    if (prefersReducedMotion() || typeof requestAnimationFrame === 'undefined' || to === 0) {
      display.value = to
      return
    }
    const from = display.value
    const start = performance.now()
    const tick = (now: number): void => {
      const t = Math.min(1, (now - start) / durationMs)
      // easeOutCubic
      const eased = 1 - Math.pow(1 - t, 3)
      display.value = from + (to - from) * eased
      if (t < 1) requestAnimationFrame(tick)
      else display.value = to
    }
    requestAnimationFrame(tick)
  }

  onMounted(() => run(target.value))
  watch(target, (to) => run(to))
  return display
}
