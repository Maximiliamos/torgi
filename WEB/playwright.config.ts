import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:8080",
    // Playwright traces include DOM input values. Production credentials must
    // never be persisted in an Actions artifact, even on a failed login.
    trace: process.env.E2E_PASSWORD ? "off" : "retain-on-failure",
    screenshot: "only-on-failure",
    ...(process.env.WEB_BASIC_AUTH_USER ? { httpCredentials: {
      username: process.env.WEB_BASIC_AUTH_USER,
      password: process.env.WEB_BASIC_AUTH_PASSWORD || ""
    }} : {})
  },
  reporter: "line"
});
