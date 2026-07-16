import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:8080",
    httpCredentials: {
      username: process.env.WEB_BASIC_AUTH_USER || "bankrotai",
      password: process.env.WEB_BASIC_AUTH_PASSWORD || "bankrotai-smoke-password"
    }
  },
  reporter: "line"
});
