// Shared pieces for the browser tests: accounts, API calls with the browser's own session, test data
// (e2e/seed.py) and stopping/starting the API of the running stack.
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, test as base, type APIResponse, type BrowserContext, type Page } from '@playwright/test';

export const ADMIN = { username: 'beheerder', password: 'lang-genoeg-wachtwoord-2026' };
export const VIEWER = { username: 'meekijker', password: 'kijken-mag-altijd-2026' };
export const HOME_NAME = 'Thuis';
export const LAN_URL = 'http://192.168.1.20:8088';

const SEED = fileURLToPath(new URL('./seed.py', import.meta.url));

function env(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`${name} ontbreekt: start de browsertests met e2e/run-stack.sh.`);
  return v;
}

/** Writes test data straight into the test database (devices, mappings, alerts, readings). See seed.py. */
export function seed<T = Record<string, unknown>>(command: 'device' | 'mapping' | 'alert' | 'resolve' | 'heartbeat' | 'readings', data: object): T {
  const out = execFileSync(env('E2E_PYTHON'), [SEED, command, JSON.stringify(data)], { encoding: 'utf8', env: process.env });
  return JSON.parse(out.trim().split('\n').at(-1) ?? '{}') as T;
}

/** Stops or starts the API process of the running stack (vite preview keeps serving the app). */
export function apiProcess(action: 'stop' | 'start') {
  execFileSync('bash', [env('E2E_RUN_STACK'), `api-${action}`], { stdio: 'inherit', env: process.env, timeout: 90_000 });
}

/** A unique suffix for names, so a test can run more than once against the same database. */
export function unique(): string {
  return Math.random().toString(36).slice(2, 7);
}

/** JSON API calls with the cookies of this browser context (and its CSRF token for changes). */
export class Api {
  constructor(private readonly context: BrowserContext) {}

  private async csrf(): Promise<Record<string, string>> {
    const c = (await this.context.cookies()).find((x) => x.name === 'shc_csrf');
    return c ? { 'X-CSRF-Token': decodeURIComponent(c.value) } : {};
  }

  private async json<T>(res: APIResponse, what: string): Promise<T> {
    if (!res.ok()) throw new Error(`${what}: ${res.status()} ${await res.text()}`);
    return (res.status() === 204 ? null : await res.json()) as T;
  }

  async get<T = any>(path: string): Promise<T> {
    return this.json<T>(await this.context.request.get(`/api/v1${path}`), `GET ${path}`);
  }

  async send<T = any>(method: 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, data?: unknown): Promise<T> {
    const res = await this.context.request.fetch(`/api/v1${path}`, { method, data, headers: await this.csrf() });
    return this.json<T>(res, `${method} ${path}`);
  }

  async login(user: { username: string; password: string }) {
    const res = await this.context.request.post('/api/v1/auth/login', { data: user });
    expect(res.status(), `inloggen als ${user.username}`).toBe(200);
  }

  async home(): Promise<{ id: string; name: string; timezone: string }> {
    const homes = await this.get<{ id: string; name: string; active: boolean; timezone: string }[]>('/homes');
    const h = homes.find((x) => x.active) ?? homes[0];
    if (!h) throw new Error('Er is nog geen woning (draai eerst de onboarding-test).');
    return h;
  }

  async zone(homeId: string, name: string): Promise<any> {
    const zones = await this.get<any[]>(`/homes/${homeId}/zones`);
    const z = zones.find((x) => x.name === name);
    if (!z) throw new Error(`Zone ${name} niet gevonden`);
    return z;
  }
}

/** Caps as the Gree adapter reports them (contract C1). */
export const GREE_CAPS = { modes: ['off', 'auto', 'cool', 'dry', 'fan', 'heat'], min_c: 16, max_c: 30, step_c: 1, fan: true, swing: true };

/** An airco unit as the worker would have stored it: online, heating at 21 °C. */
export function seedAirco(homeId: string, name: string, state: Record<string, unknown> = {}) {
  return seed<{ id: string; name: string }>('device', {
    home_id: homeId,
    provider: 'gree',
    kind: 'ac_unit',
    name,
    model: 'Gree (e2e)',
    online: true,
    capabilities: ['mode', 'target_c', 'fan', 'swing'],
    state: { power: true, mode: 'heat', target_c: 21, fan: 'auto', indoor_c: 20.5, caps: GREE_CAPS, ...state },
  });
}

/** Makes sure a zone has an online, heating airco unit; links one through the API when it has none. */
export async function ensureLinkedZone(api: Api, homeId: string, zoneName: string): Promise<any> {
  const z = await api.zone(homeId, zoneName);
  if (z.unit_linked && z.unit_status === 'online' && z.setpoint_adjustable) return z;
  const unit = seedAirco(homeId, `Airco ${zoneName} ${unique()}`);
  return api.send('PATCH', `/zones/${z.id}`, { unit_device_id: unit.id });
}

interface Fixtures {
  api: Api;
  homeId: string;
  /** A page logged in as the admin (cookie session, like a real login). */
  adminPage: Page;
}

export const test = base.extend<Fixtures>({
  api: async ({ context }, use) => {
    await use(new Api(context));
  },
  homeId: async ({ api }, use) => {
    await api.login(ADMIN);
    await use((await api.home()).id);
  },
  adminPage: async ({ page, api }, use) => {
    await api.login(ADMIN);
    await use(page);
  },
});

export { expect };

/** Fails when the page scrolls sideways (phones, 360 px). */
export async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, 'horizontale scroll in pixels').toBeLessThanOrEqual(1);
}
