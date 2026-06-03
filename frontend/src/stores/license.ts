import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

/**
 * License / entitlement state. In this release everything is free and ungated —
 * the store exists so feature checks have a single home and views can call
 * `has(...)` today without branching on whether licensing is wired yet. When a
 * signed-license backend lands, `load()` fills `tier` + `features` from the API;
 * until then it's a no-op and `has()` returns true for everything.
 */
export const useLicenseStore = defineStore('license', () => {
  const tier = ref<'free'>('free')
  const features = ref<string[]>([])
  const loaded = ref(false)

  // Ungated for now: every feature is available. Once entitlements exist this
  // becomes `features.value.includes(feature)`.
  function has(_feature: string): boolean {
    return true
  }

  const isFree = computed(() => tier.value === 'free')

  // Placeholder — no license endpoint yet. Marks the store as initialised so
  // callers can distinguish "not loaded" from "loaded, free tier".
  async function load(): Promise<void> {
    loaded.value = true
  }

  return { tier, features, loaded, isFree, has, load }
})
