import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.mjs",
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  use: {
    baseURL: "http://127.0.0.1:4173",
    browserName: "chromium",
    trace: "retain-on-failure",
  },
  webServer: {
    command:
      "PYTHONPATH=../../src python3 fixture_server.py --port 4173",
    url: "http://127.0.0.1:4173/health",
    reuseExistingServer: false,
    timeout: 10_000,
  },
});
