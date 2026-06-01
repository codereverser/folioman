<script setup lang="ts">
defineProps<{
  title: string
  subtitle?: string
}>()
</script>

<template>
  <section class="page-placeholder">
    <h1>{{ title }}</h1>
    <p v-if="subtitle" class="subtitle">{{ subtitle }}</p>
    <p class="note">This screen is a stub — its content arrives in a later step.</p>
    <slot />
  </section>
</template>

<style scoped>
.page-placeholder {
  padding: 1.5rem;
}

h1 {
  margin: 0 0 0.25rem;
  font-size: 1.5rem;
}

.subtitle {
  margin: 0 0 0.5rem;
  font-weight: 600;
}

.note {
  color: var(--p-text-muted-color, #64748b);
}
</style>
