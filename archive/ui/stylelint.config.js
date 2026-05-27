module.exports = {
  extends: ["stylelint-config-standard", "stylelint-config-prettier"],
  plugins: ["stylelint-scss"],
  // add your custom config here
  // https://stylelint.io/user-guide/configuration
  rules: {
    "color-hex-length": "long",
    "at-rule-no-unknown": null,
    "scss/at-rule-no-unknown": true,
  },
};
