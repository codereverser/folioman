<script setup lang="ts">
import { useRoute } from 'vue-router'
import PagePlaceholder from '@/components/PagePlaceholder.vue'

const route = useRoute()
</script>

<template>
  <PagePlaceholder title="Scheme detail" :subtitle="`Security #${route.params.securityId}`" />
</template>
