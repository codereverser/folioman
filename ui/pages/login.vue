<template lang="pug">
  .container.items-center.h-screen.flex.justify-center.mx-auto
    Card(class="md:w-3/5 lg:w-2/5")
      template(slot="title") Login
      template(slot="content")
        .p-inputgroup.p-input-filled
          .p-inputgroup-addon
            i.pi.pi-user
          InputText.w-full(placeholder="Username" v-model="creds.username")
        .p-inputgroup.p-input-filled.mt-4
          .p-inputgroup-addon
            i.pi.pi-lock
          InputText.w-full(placeholder="Password" type="password" v-model="creds.password")
      template(slot="footer")
        .flex.justify-center.my-4
          Button(label="Login" @click="login")
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
