<template lang="pug">
  .flex
    .fixed.z-20.inset-0.bg-black.opacity-50.transition-opacity.lg_hidden(:class="isOpen ? 'block': 'hidden'" @click="isOpen = false")
    .fixed.z-30.inset-y-0.left-0.w-64.transition.duration-300.transform.overflow-y-auto.lg_translate-x-0.lg_static.lg_inset-0.bg-gray-900(:class="isOpen ? 'translate-x-0 ease-out' : '-translate-x-full ease-in'")
      .flex.items-end.justify-end.m-2
        .flex.items-center.lg_hidden
          Button.p-button-rounded.p-button-text(icon="pi pi-times" @click="isOpen=false")
      //.flex.items-center.justify-center.mt-8
        .flex.items-center
          span.text-2xl.mx-2.font-semibold Folioman
      nav.mt-10
        nuxt-link.flex.items-center.mt-4.py-2.px-6.border-l-4(:class="$route.name === 'index' ? activeClass: inactiveClass" to="/")
          i.pi.pi-chart-bar
          span.mx-4 Dashboard
        nuxt-link.flex.items-center.mt-4.py-2.px-6.border-l-4(:class="$route.name === 'index1' ? activeClass: inactiveClass" to="/import")
          i.pi.pi-download
          span.mx-4 Import Portfolio
</template>

<script lang="ts">
import { defineComponent, ref } from "@nuxtjs/composition-api";
import { useSidebar } from "~/hooks/useSidebar";

export default defineComponent({
  setup() {
    const { isOpen } = useSidebar();
    const activeClass = ref(
      "bg-gray-600 bg-opacity-25 text-gray-100 border-gray-100"
    );
    const inactiveClass = ref(
      "border-gray-900 text-gray-500 hover_bg-gray-600 hover_bg-opacity-25 hover_text-gray-100"
    );
    return { isOpen, activeClass, inactiveClass };
  },
});
</script>
