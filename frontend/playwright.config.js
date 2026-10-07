import { defineConfig, devices } from "@playwright/test";

// Browser smoke tests (npm run e2e) against a throwaway demo instance that
// e2e/global-setup.js starts on a free port and deletes afterwards. Build
// the UI first (npm run build): the backend serves frontend/dist.
const CI = !!process.env.CI;

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.js",
  // One instance for every test, and tests that decide posts: one at a time.
  fullyParallel: false,
  workers: 1,
  forbidOnly: CI,
  retries: CI ? 1 : 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: process.env.FEEDVAULT_E2E_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop",
      testMatch: /(desktop|links|lists|duplicates|unsaved|scripts)\.spec\.js$/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      // Layout rules at four desktop sizes (the spec sets each in turn).
      name: "layout",
      testMatch: /layout\.spec\.js$/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      // 1440x900 at 125% browser zoom: a 1152x720 CSS viewport at a device
      // pixel ratio of 1.25, what the page sees under the browser's zoom.
      name: "layout-zoom",
      testMatch: /layout\.spec\.js$/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1152, height: 720 }, deviceScaleFactor: 1.25 },
    },
    {
      // Focus, hover, popovers and scrolled pages at 1280x800 and 1440x900
      // (the spec sets each in turn).
      name: "states",
      testMatch: /states\.spec\.js$/,
      // No trace: it records every key press and doubles the run; a failure
      // attaches its findings and a screenshot.
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, trace: "off" },
    },
    {
      // WCAG contrast in each preset theme.
      name: "themes",
      testMatch: /themes\.spec\.js$/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, trace: "off" },
    },
    {
      name: "phone",
      testMatch: /(phone|safe-area|touch)\.spec\.js$/,
      // 375x812 with touch (the iPhone X profile), in Chromium: the only browser installed.
      use: { ...devices["iPhone X"], browserName: "chromium" },
    },
  ],
});
