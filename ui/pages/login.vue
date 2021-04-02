<template lang="pug">
  .container.items-center.h-screen.flex.justify-center.mx-auto
    form(class="md_w-3/5 lg_w-2/5" @submit.prevent="login")
      Card
        template(#title) Login
        template(#content)
          .p-inputgroup.p-input-filled
            .p-inputgroup-addon
              i.pi.pi-user
            InputText.w-full(placeholder="Username" v-model="creds.username")
          .p-inputgroup.p-input-filled.mt-4
            .p-inputgroup-addon
              i.pi.pi-lock
            InputText.w-full(placeholder="Password" type="password" v-model="creds.password")
        template(#footer)
          .flex.justify-center.my-4
            input.p-button(type="submit" value="Login")
          .p-invalid {{ error }}
</template>

<script lang="ts">
import {
  defineComponent,
  reactive,
  ref,
  useContext,
} from "@nuxtjs/composition-api";

export default defineComponent({
  layout: "login",
  setup() {
    const { $auth } = useContext();

    const creds = reactive({
      username: "",
      password: "",
    });
    const error = ref("");
    const login = async () => {
      try {
        error.value = "";
        const { username, password } = creds;
        await $auth.loginWith("local", {
          data: { username, password },
        });
      } catch (err) {
        if (err.response) {
          const response = err.response;
          if (response.status_code === 400) {
            error.value = "Invalid parameters";
          } else {
            error.value = response.data;
          }
        } else {
          error.value = err.message;
        }
      }
    };

    return { creds, error, login };
  },
  head: {
    title: "Login",
  },
});
</script>

<style lang="scss" scoped>
.sizes {
  .p-inputtext {
    display: block;
    margin-bottom: 0.5rem;

    &:last-child {
      margin-bottom: 0;
    }
  }
}
</style>
