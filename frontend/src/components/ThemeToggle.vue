<script setup lang="ts">
import { computed } from 'vue'
import Button from 'primevue/button'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()

// Cycles light → dark → system; icon reflects the current preference.
const icon = computed(() => {
  if (ui.themePreference === 'system') return 'pi pi-desktop'
  return ui.themePreference === 'dark' ? 'pi pi-moon' : 'pi pi-sun'
})
const label = computed(() => `Theme: ${ui.themePreference} (click to change)`)
</script>

<template>
  <Button
    :icon="icon"
    severity="secondary"
    text
    rounded
    :aria-label="label"
    :title="label"
    @click="ui.toggleTheme()"
  />
</template>
