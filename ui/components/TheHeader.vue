<template lang="pug">
  .layout-topbar.flex.justify-between
    //button.layout-menu-button(@click="onMenuToggle")
      span.pi.pi-bars
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
  //.layout-topbar
    button.layout-menu-button(@click="onMenuToggle")
      span.pi.pi-bars

  //header.flex.justify-between.items-center.py-1.bg-white.border-b-2.border-indigo-200
    .flex.items-center.lg_hidden
      Button.p-button-sm.text-gray-500.p-button-rounded.p-button-text(icon="pi pi-align-justify" @click="isOpen = true")
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
