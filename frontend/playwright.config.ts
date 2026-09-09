import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e", fullyParallel: false,
  use: { baseURL: "http://127.0.0.1:3101", trace: "retain-on-failure" },
  projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"] } }, { name: "mobile", use: { ...devices["iPhone 13"], defaultBrowserType: "chromium" } }],
  webServer: { command: "npm run dev -- --port 3101", url: "http://127.0.0.1:3101", reuseExistingServer: !process.env.CI,
    env: { DIORAMA_LIBRARY_DIR: process.env.DIORAMA_E2E_LIBRARY || process.env.DIORAMA_LIBRARY_DIR || "" } },
});
