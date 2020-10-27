<template lang="pug">
  .flex.h-screen.bg-gray-200.font-roboto
    Sidebar
    .flex-1.flex.flex-col.overflow-hidden
      Header
      Toast
      main.flex-1.overflow-x-hidden.overflow-y-auto.bg-gray-200
        Nuxt
</template>

<style>
html {
  font-family: "Source Sans Pro", -apple-system, BlinkMacSystemFont, "Segoe UI",
    Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 14px;
  word-spacing: 1px;
  -ms-text-size-adjust: 100%;
  -webkit-text-size-adjust: 100%;
  -moz-osx-font-smoothing: grayscale;
  -webkit-font-smoothing: antialiased;
  box-sizing: border-box;
}
</style>
