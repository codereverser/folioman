<script setup lang="ts">
import { computed } from 'vue'
import PagePlaceholder from '@/components/PagePlaceholder.vue'
import { useRosterStore } from '@/stores/roster'
import { useUiStore } from '@/stores/ui'

const roster = useRosterStore()
const ui = useUiStore()

const name = computed(() => roster.familyName(ui.selectedFamilyId) ?? 'this family')
</script>

<template>
  <PagePlaceholder title="Family aggregate" :subtitle="name" />
</template>
