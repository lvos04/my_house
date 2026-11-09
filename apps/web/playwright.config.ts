// Browser end-to-end tests against the real stack (verbeterlijst 42). Start them with
// e2e/run-stack.sh: it starts PostgreSQL's test database, the API and `vite preview`, sets E2E_*, runs
// Playwright and cleans up. The files are named *.e2e.ts so Vitest (npm test) never picks them up.
import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.E2E_BASE_URL;
if (!baseURL) throw new Error('E2E_BASE_URL ontbreekt: start de browsertests met e2e/run-stack.sh.');

export default defineConfig({
  testDir: './e2e',
  testMatch: /.*\.e2e\.ts$/,
  outputDir: './e2e/.output/test-results',
  // One stack, one database, and one test stops the API: never run tests at the same time.
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [['list'], ['html', { outputFolder: 'e2e/.output/report', open: 'never' }]] : [['list']],
  use: {
    ...devices['Desktop Chrome'],
    baseURL,
    locale: 'nl-NL',
    timezoneId: 'Europe/Amsterdam',
    // The PWA service worker would answer page loads from its cache; the tests check the live app.
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    // The first admin and the home are made through the UI; every other test builds on them.
    { name: 'onboarding', testMatch: /01-onboarding\.e2e\.ts$/ },
    { name: 'flows', testMatch: /(0[2-9]|[1-9]\d)-.+\.e2e\.ts$/, dependencies: ['onboarding'] },
  ],
});
