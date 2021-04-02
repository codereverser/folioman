import { NuxtConfig } from "@nuxt/types";

const config: NuxtConfig = {
  // Disable server-side rendering (https://go.nuxtjs.dev/ssr-mode)
  ssr: false,

  vue: {
    config: {
      devtools: true,
      productionTip: false,
    },
  },

  // Global page headers (https://go.nuxtjs.dev/config-head)
  head: {
    title: "folioman",
    meta: [
      { charset: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { hid: "description", name: "description", content: "" },
    ],
    link: [
      { rel: "icon", type: "image/x-icon", href: "/favicon.ico" },
      {
        rel: "icon",
        type: "image/png",
        sizes: "32x32",
        href: "/favicon-32x32.png",
      },
      {
        rel: "icon",
        type: "image/png",
        sizes: "16x16",
        href: "/favicon-16x16.png",
      },
      { rel: "manifest", href: "/site.webmanifest" },
      {
        rel: "apple-touch-icon",
        sizes: "180x180",
        href: "/apple-touch-icon.png",
      },
    ],
  },

  // Global CSS (https://go.nuxtjs.dev/config-css)
  css: ["~/assets/layout/layout.scss"],

  // Plugins to run before rendering page (https://go.nuxtjs.dev/config-plugins)
  plugins: [],

  // Auto import components (https://go.nuxtjs.dev/config-components)
  components: true,

  // Modules for dev and build (recommended) (https://go.nuxtjs.dev/config-modules)
  buildModules: [
    "@nuxtjs/composition-api",
    "@nuxt/typescript-build",
    "@nuxtjs/tailwindcss",
    "@nuxtjs/stylelint-module",
  ],

  // Modules (https://go.nuxtjs.dev/config-modules)
  modules: [
    // https://go.nuxtjs.dev/axios
    "@nuxtjs/axios",
    "@nuxtjs/auth-next",
    "primevue/nuxt",
  ],

  router: {
    middleware: ["auth"],
  },

  auth: {
    strategies: {
      local: {
        scheme: "refresh",
        endpoints: {
          login: { url: "/api/auth/login", method: "post" },
          logout: { url: "/api/auth/logout", method: "post" },
          user: { url: "/api/me", method: "get" },
        },
        token: {
          property: "access",
          required: true,
        },
        refreshToken: {
          property: "refresh",
          data: "refresh",
        },
      },
    },
  },

  // Axios module configuration (https://go.nuxtjs.dev/config-axios)
  axios: {
    proxy: true,
  },

  proxy: {
    "/api/": { target: "http://127.0.0.1:8000", ws: false },
  },

  primevue: {
    theme: "saga-green",
    ripple: true,
    components: [
      "Button",
      "Card",
      "Column",
      "DataTable",
      "FileUpload",
      "InputText",
      "Panel",
      "ProgressBar",
      "ProgressSpinner",
      "Steps",
      "Toast",
    ],
  },

  stylelint: {
    fix: true,
  },

  tailwindcss: {
    config: {
      separator: "_",
      jit: true,
    },
  },

  // Build Configuration (https://go.nuxtjs.dev/config-build)
  build: {
    extend(config, { isClient }) {
      // Extend only webpack config for client-bundle
      if (isClient) {
        config.devtool = "source-map";
      }
    },
  },
};

export default config;
