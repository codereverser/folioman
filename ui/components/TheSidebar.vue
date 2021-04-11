<template lang="pug">
  sidebar-menu(
    :menu="menu"
    width="250px"
    @toggle-collapse="onToggleCollapse"
    :disableOnHover="false")
</template>

<script lang="ts">
import { defineComponent, ref } from "@nuxtjs/composition-api";

export default defineComponent({
  setup(_, { emit }) {
    const menu = ref([
      {
        component: "Logo",
        hiddenOnCollapse: true,
      },
      {
        header: true,
        title: "Mutual funds",
        hiddenOnCollapse: true,
      },
      {
        href: { path: "/" },
        title: "Dashboard",
        icon: "pi pi-chart-bar",
        attributes: { exact: true },
      },
      {
        href: "/mutualfunds/schemes",
        title: "Portfolio",
        icon: "pi pi-list",
      },
      {
        title: "Analysis",
        icon: "pi pi-chart-line",
        child: [
          {
            href: "/mutualfunds/analysis/whatif",
            icon: "pi pi-question-circle",
            title: "What If?",
          },
          {
            href: "/mutualfunds/analysis/overlap",
            icon: "pi pi-copy",
            title: "Overlap",
          },
        ],
      },
      {
        title: "Import Portfolio",
        icon: "pi pi-cloud-upload",
        href: { path: "/import" },
        attributes: { exact: false },
      },
      { header: true, title: "Stocks", hiddenOnCollapse: true },
      {
        title: "Dashboard",
        icon: "pi pi-money-bill",
        badge: { text: "WIP", class: "vsm--badge_default" },
        disabled: true,
      },
      { header: true, title: "Crypto", hiddenOnCollapse: true },
      {
        title: "Dashboard",
        icon: "pi pi-wallet",
        badge: { text: "WIP", class: "vsm--badge_default" },
        disabled: true,
      },
    ]);
    const onToggleCollapse = () => {
      emit("sidebar-collapse");
    };
    return { menu, onToggleCollapse };
  },
});
</script>

<style lang="scss">
.v-sidebar-menu {
  .vsm--item {
    .nuxt-link-active {
      @apply text-primary;
    }
  }
}
</style>
