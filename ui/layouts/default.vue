<template lang="pug">
  div(:class="containerClass" @click="onWrapperClick")
    TheHeader(@menu-toggle="onMenuToggle")
    transition(name="layout-sidebar")
      TheSidebar(:class="sidebarClass" @click="onSidebarClick")
    main.layout-main
      Nuxt
  //.flex.h-screen.bg-gray-200.font-roboto
    Sidebar
    .flex-1.flex.flex-col.overflow-hidden
      Header
      Toast
      main.flex-1.overflow-x-hidden.overflow-y-auto.bg-gray-200
        Nuxt
</template>

<script lang="ts">
import { computed, defineComponent, ref } from "@nuxtjs/composition-api";

export default defineComponent({
  setup() {
    const layoutMode = ref("static");
    const layoutColorMode = ref("dark");
    const staticMenuInactive = ref(false);
    const overlayMenuActive = ref(false);
    const mobileMenuActive = ref(false);
    // const menuActive = ref(false);
    const menuClick = ref(false);

    const isDesktop = () => window.innerWidth > 1024;
    const isSidebarVisible = () => {
      if (isDesktop()) {
        if (layoutMode.value === "static") return !staticMenuInactive.value;
        else if (layoutMode.value === "overlay") return overlayMenuActive.value;
        else return true;
      } else {
        return true;
      }
    };

    const containerClass = computed(() => {
      return [
        "layout-wrapper",
        {
          "layout-overlay": layoutMode.value === "overlay",
          "layout-static": layoutMode.value === "static",
          "layout-static-sidebar-inactive":
            staticMenuInactive.value && layoutMode.value === "static",
          "layout-overlay-sidebar-active":
            overlayMenuActive.value && layoutMode.value === "overlay",
          "layout-mobile-sidebar-active": mobileMenuActive.value,
        },
      ];
    });
    const sidebarClass = computed(() => {
      return [
        "layout-sidebar",
        {
          "layout-sidebar-dark": layoutColorMode.value === "dark",
          "layout-sidebar-light": layoutColorMode.value === "light",
        },
      ];
    });

    const onWrapperClick = () => {
      if (!menuClick.value) {
        overlayMenuActive.value = false;
        mobileMenuActive.value = false;
      }
      menuClick.value = false;
    };
    const onMenuToggle = (event: Event) => {
      menuClick.value = true;
      if (isDesktop()) {
        if (layoutMode.value === "overlay") {
          if (mobileMenuActive.value === true) {
            overlayMenuActive.value = true;
          }
          overlayMenuActive.value = !overlayMenuActive.value;
          mobileMenuActive.value = false;
        } else if (layoutMode.value === "static") {
          staticMenuInactive.value = !staticMenuInactive.value;
        }
      } else {
        mobileMenuActive.value = !mobileMenuActive.value;
      }
      event.preventDefault();
    };

    const onSidebarClick = () => {
      menuClick.value = true;
    };

    // const onMenuItemClick = (event: Event) => {
    //   if (event.item && !event.item.items) {
    //     overlayMenuActive.value = false;
    //     mobileMenuActive.value = false;
    //   }
    // };

    const onLayoutChange = (newValue: string) => {
      layoutMode.value = newValue;
    };

    const onLayoutColorChange = (newValue: string) => {
      layoutColorMode.value = newValue;
    };

    return {
      containerClass,
      sidebarClass,
      isSidebarVisible,
      layoutMode,
      onLayoutChange,
      onLayoutColorChange,
      // onMenuItemClick,
      onMenuToggle,
      onSidebarClick,
      onWrapperClick,
      mobileMenuActive,
      overlayMenuActive,
      staticMenuInactive,
    };
  },
});
</script>
<!--<style>-->
<!--html {-->
<!--  font-family: "Source Sans Pro", -apple-system, BlinkMacSystemFont, "Segoe UI",-->
<!--    Roboto, "Helvetica Neue", Arial, sans-serif;-->
<!--  font-size: 14px;-->
<!--  word-spacing: 1px;-->
<!--  -ms-text-size-adjust: 100%;-->
<!--  -webkit-text-size-adjust: 100%;-->
<!--  -moz-osx-font-smoothing: grayscale;-->
<!--  -webkit-font-smoothing: antialiased;-->
<!--  box-sizing: border-box;-->
<!--}-->
<!--</style>-->
