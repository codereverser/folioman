<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import InputText from 'primevue/inputtext'
import Password from 'primevue/password'
import Button from 'primevue/button'
import Message from 'primevue/message'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const error = ref('')
const submitting = ref(false)

async function submit(): Promise<void> {
  if (submitting.value || !username.value || !password.value) return
  submitting.value = true
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : null
    await router.replace(redirect ?? { name: 'home' })
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'Sign in failed.'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="login">
    <form class="card" @submit.prevent="submit">
      <img class="logo" src="/logo.svg" alt="Folioman" height="36" />
      <h1>Sign in</h1>
      <p class="muted">Enter your Folioman credentials to continue.</p>

      <template v-if="error">
        <Message severity="error" :closable="false">{{ error }}</Message>
        <p class="hint">
          Forgot your password? Reset it from the server shell:
          <code
            >docker compose -f server/docker-compose.yml exec app django-admin changepassword
            &lt;username&gt;</code
          >
          See the install guide for details.
        </p>
      </template>

      <label class="field">
        <span>Username</span>
        <InputText v-model="username" autocomplete="username" autofocus :disabled="submitting" />
      </label>

      <label class="field">
        <span>Password</span>
        <Password
          v-model="password"
          :feedback="false"
          toggle-mask
          input-class="w-full"
          autocomplete="current-password"
          :disabled="submitting"
          fluid
        />
      </label>

      <Button
        type="submit"
        label="Sign in"
        icon="pi pi-sign-in"
        :loading="submitting"
        :disabled="!username || !password"
      />
    </form>
  </main>
</template>

<style scoped>
.login {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100dvh;
  padding: var(--fm-space-5);
}
.card {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-4);
  width: 100%;
  max-width: 22rem;
  padding: var(--fm-space-8) var(--fm-space-6);
  background: var(--fm-surface);
  border: 1px solid var(--fm-border);
  border-radius: var(--fm-radius-xl);
  box-shadow: var(--fm-shadow-sm);
}
.logo {
  align-self: flex-start;
}
h1 {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 600;
}
.muted {
  margin: 0;
  color: var(--fm-text-muted);
  font-size: 0.875rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-2);
  font-size: 0.875rem;
  font-weight: 500;
}
.field :deep(.p-inputtext),
.field :deep(.p-password) {
  width: 100%;
}
.hint {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.hint code {
  display: block;
  margin-top: var(--fm-space-2);
  padding: var(--fm-space-2);
  font-size: 0.75rem;
  word-break: break-all;
  background: var(--fm-surface-raised);
  border-radius: var(--fm-radius-md);
}
</style>
