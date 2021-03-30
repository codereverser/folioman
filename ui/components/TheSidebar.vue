<template lang="pug">
  .flex.flex-col
    .flex.justify-center.items-center.p-1.w-full.border-b-2.border-r-2.border-gray-200(style="height: 50px")
      Logo.h-12.w-12
      .font-semibold.text-secondary.text-2xl.mx-2 folioman
    nav.mt-5
      NuxtLink.flex.items-center.mt-4.py-2.px-6(to="/" exact)
        i.pi.pi-chart-bar
        span.mx-4 Dashboard
      NuxtLink.flex.items-center.mt-4.py-2.px-6(to="/import")
        i.pi.pi-download
        span.mx-4 Import Portfolio
</template>

<style lang="scss" scoped>
a {
  @apply font-semibold text-base text-primary hover_bg-primary hover_text-white rounded mx-4;

  &.nuxt-link-active {
    @apply bg-primary text-white bg-opacity-70;
  }
}
</style>
