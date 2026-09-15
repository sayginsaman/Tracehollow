import { defineConfig, devices } from "@playwright/test";

/**
 * Browser end-to-end tests against an already running stack (see e2e/README.md).
 * They never start services themselves and use only synthetic data.
 */
const executablePath = process.env.TRACEHOLLOW_E2E_CHROMIUM_EXECUTABLE || undefined;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  outputDir: "test-results",
  use: {
    baseURL: process.env.TRACEHOLLOW_E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    acceptDownloads: true,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1400, height: 1000 }, launchOptions: { executablePath } },
    },
  ],
});
