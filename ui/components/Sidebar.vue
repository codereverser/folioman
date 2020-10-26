<template lang="pug">
  .flex
    .fixed.z-20.inset-0.bg-black.opacity-50.transition-opacity.lg_hidden(:class="isOpen ? 'block': 'hidden'" @click="isOpen = false")
    .fixed.z-30.inset-y-0.left-0.w-64.transition.duration-300.transform.overflow-y-auto.lg_translate-x-0.lg_static.lg_inset-0.bg-gray-900(:class="isOpen ? 'translate-x-0 ease-out' : '-translate-x-full ease-in'")
      //.flex.items-center.justify-end.lg_hidden
          Button.p-button-rounded.p-button-text(icon="pi pi-times" @click="isOpen=false")
      .flex.items-center.justify-center.m-2
        .flex.items-center
          svg.h-12.w-12(x="0px" y="0px" viewBox="0 0 610 370" xmlns="http://www.w3.org/2000/svg")
            g(fill="none")
              path(
                fill="#41b883"
                d="M 161 177.893 L 161 113.893 C 161 96.229 175.336 81.893 193 81.893 L 257 81.893 L 257 1.893 L 161 1.893 C 107.976 1.893 65 44.869 65 97.893 L 65 177.893 L 1 177.893 L 1 257.893 L 65 257.893 L 65 367.893 L 161 367.893 L 161 257.893 L 225 257.893 L 257 177.893 L 161 177.893 Z"
              )
              path(
                fill="#34495e"
                d="M 213 81.893 L 257 81.893 C 271.336 81.893 289 96.229 289 113.893 L 289 367.893 L 385 367.893 L 385 113.893 C 385 96.229 399.336 81.893 417 81.893 L 481 81.893 C 495.336 81.893 513 96.229 513 113.893 L 513 367.893 L 609 367.893 L 609 97.893 C 609 44.869 566.024 1.893 513 1.893 L 463 1.893 C 443 1.893 345.566 -8.537 337 41.893 C 319.995 -5.893 233 1.893 213 1.893"
              )
          span.text-white.text-2xl.mx-2.font-semibold Folio Manager
      nav.mt-10
        NuxtLink.flex.items-center.mt-4.py-2.px-6.border-l-4(:class="$route.name === 'index' ? activeClass: inactiveClass" to="/")
          i.pi.pi-chart-bar
          span.mx-4 Dashboard
        NuxtLink.flex.items-center.mt-4.py-2.px-6.border-l-4(:class="$route.name.startsWith('import') ? activeClass: inactiveClass" to="/import")
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
