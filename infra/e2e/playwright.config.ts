// Browser tests against the compose stack (verbeterlijst 42): nginx + API + worker from the real
// images, started by infra/scripts/e2e_compose.sh with infra/e2e/compose.e2e.yaml. Runs a smoke subset
// of apps/web/e2e through nginx (first start, live updates over the WebSocket, nginx's answers while the
// API is stopped and the recovery) and worker-command.e2e.ts, which pairs the fake airco and sees a
// command confirmed by the worker.
//
// Everything else (browser, locale, time zone, timeouts) comes from apps/web/playwright.config.ts, so
// both runs behave the same. Only types are imported from @playwright/test here (no node_modules
// next to this file); the tests load it through apps/web/e2e/helpers.ts.
import type { PlaywrightTestConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import base from '../../apps/web/playwright.config';

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const WEB_E2E = here('../../apps/web/e2e');

// SHC_E2E_FLOWS: which apps/web/e2e flows run after the first start (file name prefixes), or "all".
// nginx brakes logins (10 per minute, burst 5; every flow logs in two or three times), so a long list
// run back to back gets "429" by design; the default stays well inside that.
const flows = (process.env.SHC_E2E_FLOWS ?? '07-notifications 08-offline').trim();
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const flowMatch =
  flows === 'all' ? /(0[2-9]|[1-9]\d)-.+\.e2e\.ts$/ : new RegExp(`(${flows.split(/[\s,]+/).filter(Boolean).map(escape).join('|')})[^/]*\\.e2e\\.ts$`);

const config: PlaywrightTestConfig = {
  ...base,
  outputDir: here('./.output/test-results'),
  reporter: process.env.CI ? [['list'], ['html', { outputFolder: here('./.output/report'), open: 'never' }]] : [['list']],
  projects: [
    // The first admin and the home, through the UI on an empty database (fresh data folder).
    { name: 'onboarding', testDir: WEB_E2E, testMatch: /01-onboarding\.e2e\.ts$/ },
    { name: 'flows', testDir: WEB_E2E, testMatch: flowMatch, dependencies: ['onboarding'] },
    // Compose only: a command executed by the worker and confirmed by the (fake) airco.
    { name: 'worker', testDir: here('.'), testMatch: /.*\.e2e\.ts$/, dependencies: ['onboarding'] },
  ],
};

export default config;
