<template lang="pug">
  .layout-topbar.flex.justify-between
    .flex.items-center.mx-4
      Button.p-button-lg.p-button-text(icon="pi pi-bars" @click="onMenuToggle")
      span {{ title }}
    span &nbsp;
    .flex.items-center
      .relative
        Button.p-button-text.p-button-sm(@click="dropdownOpen = !dropdownOpen" :icon="profileIcon" :label="user" iconPos="right")
        .fixed.inset-0.h-full.w-full.z-10(v-show="dropdownOpen" @click="dropdownOpen = false")
        .absolute.right-0.mt-2.py-2.w-48.rounded-md.shadow-xl.z-20(v-show="dropdownOpen")
          nuxt-link.block.px-4.py-2.text-sm.dropdown-item(to="/") Profile
          a.block.px-4.py-2.text-sm.dropdown-item(@click="logout") Log Out
</template>

<script lang="ts">
import {
  defineComponent,
  computed,
  ref,
  useContext,
  useRouter,
} from "@nuxtjs/composition-api";
import { RefreshScheme } from "@nuxtjs/auth-next";
// import { useSidebar } from "~/hooks/useSidebar";

export default defineComponent({
  setup(_, { root, emit }) {
    const { $auth } = useContext();
    const strategy = $auth.strategy as RefreshScheme;
    const router = useRouter();

    const dropdownOpen = ref(false);
    const profileIcon = computed(() => {
      return dropdownOpen.value ? "pi pi-angle-up" : "pi pi-angle-down";
    });
    const user = $auth.user;

    const onMenuToggle = (event: Event) => {
      emit("menu-toggle", event);
    };

    const title = computed(() => root.$meta().refresh().metaInfo.titleChunk);

    const logout = async () => {
      await $auth.logout({
        data: { refresh: strategy.refreshToken.get() },
      });
      await router.push("/login");
    };

    return { dropdownOpen, onMenuToggle, profileIcon, title, user, logout };
  },
});
</script>

<style lang="scss" scoped>
.dropdown-item:hover {
  background-color: var(--primary-color);
  color: var(--primary-color-text);
}
</style>
