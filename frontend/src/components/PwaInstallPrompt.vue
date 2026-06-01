<script setup lang="ts">
import { ref } from 'vue'
import Button from 'primevue/button'
import Message from 'primevue/message'
import { usePwaInstall } from '@/composables/usePwaInstall'

const { canInstall, isIos, isStandalone, promptInstall } = usePwaInstall()

// One-time dismissal of the iOS tooltip (Android uses the native prompt).
const IOS_TIP_KEY = 'folioman.iosInstallTipDismissed'
const iosTipDismissed = ref(
  typeof localStorage !== 'undefined' && localStorage.getItem(IOS_TIP_KEY) === '1',
)

function dismissIosTip(): void {
  iosTipDismissed.value = true
  if (typeof localStorage !== 'undefined') localStorage.setItem(IOS_TIP_KEY, '1')
}
</script>

<template>
  <div v-if="!isStandalone" class="pwa-install">
    <!-- Android / Chromium: native install prompt behind a button. -->
    <Button
      v-if="canInstall"
      icon="pi pi-download"
      label="Install Folioman"
      size="small"
      @click="promptInstall"
    />

    <!-- iOS Safari: no install event — show the share-sheet instructions once. -->
    <Message
      v-else-if="isIos && !iosTipDismissed"
      severity="info"
      :closable="true"
      @close="dismissIosTip"
    >
      Install: tap <i class="pi pi-upload" aria-label="Share" /> Share, then
      <strong>Add to Home Screen</strong>.
    </Message>
  </div>
</template>

<style scoped>
.pwa-install {
  display: flex;
  justify-content: center;
}
</style>
