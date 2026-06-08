<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import InputText from 'primevue/inputtext'
import Password from 'primevue/password'
import Button from 'primevue/button'
import Message from 'primevue/message'
import { useAuthStore } from '@/stores/auth'
import { createFirstAdmin, fetchSetupState, markSetupComplete } from '@/api/setup'

const auth = useAuthStore()
const router = useRouter()

const username = ref('')
const password = ref('')
const confirm = ref('')
const email = ref('')
const token = ref('')
const tokenRequired = ref(false)
const error = ref('')
const submitting = ref(false)

onMounted(async () => {
  // The guard already routed us here, so this is a cache hit — it just tells us
  // whether to show the console-token field.
  tokenRequired.value = (await fetchSetupState()).token_required
})

async function submit(): Promise<void> {
  if (submitting.value || !username.value || !password.value) return
  if (tokenRequired.value && !token.value) {
    error.value = 'Enter the setup token shown in the server console.'
    return
  }
  if (password.value !== confirm.value) {
    error.value = 'Passwords do not match.'
    return
  }
  submitting.value = true
  error.value = ''
  try {
    const tokens = await createFirstAdmin(username.value, password.value, email.value, token.value)
    auth.setTokens(tokens.access, tokens.refresh)
    markSetupComplete()
    await router.replace({ name: 'home' })
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'Setup failed.'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="setup">
    <form class="card" @submit.prevent="submit">
      <img class="logo" src="/logo.svg" alt="Folioman" height="36" />
      <h1>Create your admin account</h1>
      <p class="muted">
        This is the first time Folioman has started on this server. Create the
        administrator login — you'll use it to sign in from now on.
      </p>

      <Message v-if="error" severity="error" :closable="false">{{ error }}</Message>

      <label v-if="tokenRequired" class="field">
        <span>Setup token</span>
        <InputText
          v-model="token"
          autocomplete="off"
          :disabled="submitting"
          placeholder="Shown in the server console"
        />
        <small class="muted"
          >Folioman printed this to the server console (e.g. <code>docker compose logs app</code>)
          on first start.</small
        >
      </label>

      <label class="field">
        <span>Username</span>
        <InputText v-model="username" :autofocus="!tokenRequired" autocomplete="username" :disabled="submitting" />
      </label>

      <label class="field">
        <span>Email <small>(optional)</small></span>
        <InputText v-model="email" type="email" autocomplete="email" :disabled="submitting" />
      </label>

      <label class="field">
        <span>Password</span>
        <Password
          v-model="password"
          :feedback="false"
          toggle-mask
          autocomplete="new-password"
          :disabled="submitting"
          fluid
        />
      </label>

      <label class="field">
        <span>Confirm password</span>
        <Password
          v-model="confirm"
          :feedback="false"
          toggle-mask
          autocomplete="new-password"
          :disabled="submitting"
          fluid
        />
      </label>

      <Button
        type="submit"
        label="Create account"
        icon="pi pi-user-plus"
        :loading="submitting"
        :disabled="!username || !password || !confirm"
      />

      <p class="hint">
        You can change this password later from the server shell:
        <code>docker compose -f server/docker-compose.yml exec app django-admin changepassword
        &lt;username&gt;</code>
      </p>
    </form>
  </main>
</template>

<style scoped>
.setup {
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
  max-width: 36rem;
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
