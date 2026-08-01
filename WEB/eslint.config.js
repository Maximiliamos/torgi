import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "playwright-report", "test-results"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      globals: { window: "readonly", document: "readonly", fetch: "readonly", URL: "readonly", Response: "readonly" }
    },
    rules: { "@typescript-eslint/no-explicit-any": "off" }
  }
);
