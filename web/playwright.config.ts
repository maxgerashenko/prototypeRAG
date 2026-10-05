import { defineConfig, devices } from "@playwright/test";

// Browser tests for the built pages (`npm run e2e`). The backend is mocked per test
// (e2e/mocks.ts: HTTP routes + a scripted /voice/browser WebSocket), so only the static
// build is served: no Postgres, LM Studio or Google Speech needed. Chromium's fake mic
// supplies audio. First run on a new machine: `npx playwright install chromium`.
const PORT = 4173;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  reporter: process.env.CI ? "line" : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    timezoneId: "UTC", // date labels ("Today · 2:58 PM") are asserted exactly
    permissions: ["microphone"],
    launchOptions: {
      args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
    },
  },
  projects: [
    { name: "phone", use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 } } },
  ],
  webServer: {
    command: `npm run build && npx vite preview --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}/web/`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
