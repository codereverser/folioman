<template lang="pug">
  div(:class="containerClass" @click="onWrapperClick")
    TheHeader(@menu-toggle="onMenuToggle")
    Toast
    TheSidebar(:class="sidebarClass"
               @click="onSidebarClick"
               @sidebar-collapse="onSidebarCollapseToggle")
    main.layout-main
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
    const menuClick = ref(false);

    const isDesktop = () => window.innerWidth > 1024;
    const isSidebarCollapsed = ref(false);
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
          "layout-sidebar-collapsed": isSidebarCollapsed.value,
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

    const onSidebarCollapseToggle = () => {
      isSidebarCollapsed.value = !isSidebarCollapsed.value;
    };

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
      onMenuToggle,
      onSidebarClick,
      onSidebarCollapseToggle,
      onWrapperClick,
      mobileMenuActive,
      overlayMenuActive,
      staticMenuInactive,
    };
  },
});
</script>

