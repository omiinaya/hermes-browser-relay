import { defineConfig } from "eslint/config";
import globals from "globals";
import js from "@eslint/js";

export default defineConfig([
  { files: ["**/*.{js,mjs,cjs}"] },
  { ignores: ["**/*.min.js", "extension/content-script.js"] },
  {
    files: ["**/*.{js,mjs,cjs}"],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.webextensions,
        chrome: "readonly",
        __hermes: "readonly",
      },
    },
  },
  {
    files: ["**/*.{js,mjs,cjs}"],
    plugins: { js },
    extends: ["js/recommended"],
    rules: {
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_", caughtErrors: "none" }],
      "no-undef": "error",
    },
  },
  {
    files: ["tests/extensions/*.test.js"],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
  },
]);
