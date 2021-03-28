<template lang="pug">
  .layout-topbar.flex.justify-between
    .flex.items-center.mx-4
      Button.p-button-lg.p-button-text(icon="pi pi-bars" @click="onMenuToggle")
    span &nbsp;
    .flex.items-center
      .relative
        Button.p-button-text.p-button-sm(@click="dropdownOpen = !dropdownOpen" :icon="profileIcon" :label="user" iconPos="right")
        .fixed.inset-0.h-full.w-full.z-10(v-show="dropdownOpen" @click="dropdownOpen = false")
        .absolute.right-0.mt-2.py-2.w-48.rounded-md.shadow-xl.z-20(v-show="dropdownOpen")
          nuxt-link.block.px-4.py-2.text-sm.dropdown-item(to="/") Profile
          nuxt-link.block.px-4.py-2.text-sm.dropdown-item(to="/") Log Out
</template>

<script lang="ts">
import {
  defineComponent,
  computed,
  ref,
  useContext,
} from "@nuxtjs/composition-api";
// import { useSidebar } from "~/hooks/useSidebar";

export default defineComponent({
  setup(_, { emit }) {
    const { $auth } = useContext();
    const onMenuToggle = (event: Event) => {
      emit("menu-toggle", event);
    };
    const dropdownOpen = ref(false);
    // const { isOpen } = useSidebar();
    const profileIcon = computed(() => {
      return dropdownOpen.value ? "pi pi-angle-up" : "pi pi-angle-down";
    });
    const user = $auth.user;
    // return { dropdownOpen, isOpen, profileIcon, user };

    return { dropdownOpen, onMenuToggle, profileIcon, user };
  },
});
</script>

<style lang="scss" scoped>
.dropdown-item:hover {
  background-color: var(--primary-color);
  color: var(--primary-color-text);
}
</style>
