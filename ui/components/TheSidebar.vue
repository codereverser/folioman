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
      // {
      //   header: true,
      //   title: "Main Navigation",
      //   hiddenOnCollapse: true,
      // },
      {
        href: "/",
        title: "Dashboard",
        icon: "pi pi-chart-bar",
      },
      {
        header: true,
        title: "Mutual funds",
        hiddenOnCollapse: true,
      },
      // {
      //   href: "/",
      //   title: "Dashboard",
      //   icon: "pi pi-chart-bar",
      //   // child: [
      //   //   {
      //   //     href: "/charts/sublink",
      //   //     title: "Sub Link",
      //   //   },
      //   // ],
      // },
      {
        href: "/mutualfunds/schemes",
        title: "Portfolio",
        icon: "pi pi-list",
      },
      {
        title: "Analysis",
        icon: "pi pi-angle-left",
        child: [
          {
            href: "/mutualfunds/analysis/whatif",
            icon: "pi pi-chart-line",
            title: "What If?",
          },
          {
            href: "/mutualfunds/analysis/overlap",
            icon: "pi pi-chart-bar",
            title: "Overlap",
          },
        ],
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
//a {
//  @apply font-semibold text-base text-primary hover_bg-primary hover_text-white rounded mx-4;
//
//  &.nuxt-link-active {
//    @apply bg-primary text-white bg-opacity-70;
//  }
//}
//.v-sidebar-menu {
//  @apply bg-background border-r-2 border-gray-200;
//
//  .vsm--header {
//    @apply text-secondary font-semibold uppercase;
//  }
//
//  .vsm--link {
//    @apply font-semibold text-base text-primary hover_bg-primary hover_text-white;
//  }
//
//  .vsm--link_level-1 {
//    .vsm--icon {
//      @apply font-semibold text-base text-primary hover_bg-primary hover_text-white bg-transparent;
//    }
//  }
//}
</style>
