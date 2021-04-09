module.exports = {
  mode: "jit",
  purge: [
    "./components/**/*.{vue,js,ts}",
    "./layouts/**/*.{vue,js,ts}",
    "./pages/**/*.{vue,js,ts}",
    "./plugins/**/*.{vue,js,ts}",
    "./nuxt.config.{js,ts}",
  ],
  darkMode: "media",
  theme: {
    extend: {
      colors: {
        primary: "#4CAF50",
        secondary: "#6c757d",
      },
    },
  },
  variants: {
    extend: {},
  },
  plugins: [],
};
