<template lang="pug">
  .flex.flex-col.fm-logo
    .flex.justify-center.items-center.p-1.w-full(style="height: 50px")
      svg.h-12.w-12(x="0px" y="0px" viewBox="0 0 650 370" xmlns="http://www.w3.org/2000/svg")
        g(fill="none" transform="matrix(1, 0, 0, 1, 35, 0)")
          path(fill="#4CAF50"
            d="M 161 177.893 L 161 113.893 C 161 96.229 175.336 81.893 193 81.893 L 257 81.893 L 257 1.893 L 161 1.893 C 107.976 1.893 65 44.869 65 97.893 L 65 177.893 L 1 177.893 L -31 257.893 L 65 257.893 L 65 367.893 L 161 367.893 L 161 257.893 L 225 257.893 L 257 177.893 L 161 177.893 Z"
          )
          path(fill="#6c757d"
            d="M 213 81.893 L 257 81.893 C 271.336 81.893 289 96.229 289 113.893 L 289 367.893 L 385 367.893 L 385 113.893 C 385 96.229 399.336 81.893 417 81.893 L 481 81.893 C 495.336 81.893 513 96.229 513 113.893 L 513 367.893 L 609 367.893 L 609 97.893 C 609 44.869 566.024 1.893 513 1.893 L 463 1.893 C 443 1.893 345.566 -8.537 337 41.893 C 319.995 -5.893 233 1.893 213 1.893"
          )
      .font-semibold.text-secondary.text-2xl.mx-2 folioman
</template>

<style lang="scss">
.NuxtLogo {
  animation: 1s appear;
  margin: auto;
}

.fm-logo {
  @apply mb-4;
}

@keyframes appear {
  0% {
    opacity: 0;
  }
}
</style>
