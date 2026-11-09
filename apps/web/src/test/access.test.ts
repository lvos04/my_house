import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ACCESS_RELOAD_KEY,
  ACCESS_RELOAD_PARAM,
  AccessLoginRequired,
  accessLoginUrl,
  api,
  ApiError,
  isForeignResponse,
  isTransientError,
  NetworkError,
  onConnectionEvent,
  reloadForAccess,
  setAccessHandler,
  uploadWithProgress,
  type ConnectionEvent,
} from '../api/client';
import { FakeXhr } from './helpers';

afterEach(() => vi.unstubAllGlobals());

function store() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m };
}

describe('Cloudflare Access detection (item 38)', () => {
  it('flags redirects and HTML answers as foreign, not JSON', () => {
    const h = (ct: string) => new Headers({ 'content-type': ct });
    expect(isForeignResponse({ type: 'opaqueredirect', status: 0, redirected: false, url: '', headers: h('') })).toBe(true);
    expect(isForeignResponse({ type: 'basic', status: 302, redirected: false, url: '', headers: h('') })).toBe(true);
    expect(isForeignResponse({ type: 'basic', status: 200, redirected: true, url: 'https://team.cloudflareaccess.com/cdn-cgi/access/login', headers: h('text/html') })).toBe(true);
    expect(isForeignResponse({ type: 'basic', status: 200, redirected: false, url: '', headers: h('text/html; charset=utf-8') })).toBe(true);
    expect(isForeignResponse({ type: 'basic', status: 200, redirected: false, url: '', headers: h('application/json') })).toBe(false);
    expect(isForeignResponse({ type: 'basic', status: 503, redirected: false, url: '', headers: h('application/json') })).toBe(false);
    expect(isForeignResponse({ type: 'basic', status: 204, redirected: false, url: '', headers: h('') })).toBe(false);
  });

  it('request(): an Access redirect triggers the reload handler and throws AccessLoginRequired', async () => {
    const handler = vi.fn();
    const prev = setAccessHandler(handler);
    const events: ConnectionEvent[] = [];
    const off = onConnectionEvent((e) => events.push(e));
    vi.stubGlobal('fetch', vi.fn(async () => Response.redirect('https://team.cloudflareaccess.com/cdn-cgi/access/login', 302)));
    try {
      await expect(api.homes()).rejects.toBeInstanceOf(AccessLoginRequired);
      expect(handler).toHaveBeenCalledTimes(1);
      const init = (vi.mocked(fetch).mock.calls[0] as unknown as [string, RequestInit])[1];
      expect(init.redirect).toBe('manual');
      expect(events).toContainEqual({ type: 'unreachable' });
    } finally {
      setAccessHandler(prev);
      off();
    }
  });

  it('request(): a 200 HTML login page or an unparseable body is foreign too', async () => {
    const handler = vi.fn();
    const prev = setAccessHandler(handler);
    try {
      vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>Sign in</html>', { status: 200, headers: { 'content-type': 'text/html' } })));
      await expect(api.homes()).rejects.toBeInstanceOf(AccessLoginRequired);
      vi.stubGlobal('fetch', vi.fn(async () => new Response('<!doctype html>', { status: 200 })));
      await expect(api.homes()).rejects.toBeInstanceOf(AccessLoginRequired);
      expect(handler).toHaveBeenCalledTimes(2);
    } finally {
      setAccessHandler(prev);
    }
  });

  it('reloads once with a cache-busting parameter, then reports a loop', () => {
    const s = store();
    const loc = { href: 'https://huis.example.nl/klimaat?x=1', replace: vi.fn() };
    expect(reloadForAccess(loc, s, 1_000_000)).toBe('reloaded');
    expect(loc.replace).toHaveBeenCalledWith(`https://huis.example.nl/klimaat?x=1&${ACCESS_RELOAD_PARAM}=1000000`);
    expect(s.m.get(ACCESS_RELOAD_KEY)).toBe('1000000');
    expect(reloadForAccess(loc, s, 1_030_000)).toBe('loop');
    expect(loc.replace).toHaveBeenCalledTimes(1);
    expect(reloadForAccess(loc, s, 1_000_000 + 61_000)).toBe('reloaded');
  });

  it('builds the Access login URL back to the current page', () => {
    expect(accessLoginUrl({ href: `https://huis.example.nl/energie?${ACCESS_RELOAD_PARAM}=5` })).toBe(
      `/cdn-cgi/access/login?redirect_url=${encodeURIComponent('https://huis.example.nl/energie')}`,
    );
  });
});

describe('api client errors', () => {
  it('nginx api_unavailable is transient "starting", with Retry-After', async () => {
    const events: ConnectionEvent[] = [];
    const off = onConnectionEvent((e) => events.push(e));
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: { code: 'api_unavailable', message: 'De app start op' } }), { status: 503, headers: { 'content-type': 'application/json', 'retry-after': '5' } })),
    );
    const err = await api.homes().catch((e) => e);
    off();
    expect(err).toBeInstanceOf(ApiError);
    expect(err.retryAfter).toBe(5);
    expect(isTransientError(err)).toBe(true);
    expect(events).toContainEqual({ type: 'starting', message: 'De app start op' });
    expect(isTransientError(new NetworkError())).toBe(true);
    expect(isTransientError(new AccessLoginRequired())).toBe(false);
  });

  it('401 outside the login form reports auth_required; a wrong login does not', async () => {
    const events: ConnectionEvent[] = [];
    const off = onConnectionEvent((e) => events.push(e));
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: { code: 'auth_required', message: 'Log in' } }), { status: 401, headers: { 'content-type': 'application/json' } })));
    await api.login('a', 'b').catch(() => null);
    expect(events.some((e) => e.type === 'auth_required')).toBe(false);
    await api.homes().catch(() => null);
    off();
    expect(events).toContainEqual({ type: 'auth_required', code: 'auth_required', message: 'Log in' });
  });

  it('keeps extra detail fields (validation errors, diagnostics)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: { code: 'validation_error', message: 'Twee fouten', errors: ['a', 'b'] } }), { status: 422, headers: { 'content-type': 'application/json' } })),
    );
    const err = (await api.putButtonMappings('b1', []).catch((e) => e)) as ApiError;
    expect(err.detail.errors).toEqual(['a', 'b']);
  });

  it('sends the new endpoint bodies', async () => {
    const calls: [string, RequestInit][] = [];
    vi.stubGlobal('fetch', vi.fn(async (u: string, i: RequestInit) => {
      calls.push([u, i]);
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
    }));
    await api.changePassword({ old_password: 'oud', new_password: 'nieuw-wachtwoord', keep_phones: false });
    await api.createPairingCode({ label: 'Tel', role: 'admin', password: 'geheim' });
    await api.createPairingCode('Oud');
    await api.patchUser('u2', { disabled: true });
    await api.audit({ before: 120, limit: 50 });
    await api.unbindDevice('d1', 'airco');
    await api.putZonePolicy('z1', { watchdog_minutes: 0 });
    await api.putBackupSettings({ hour: 4, keep: 10 });
    await api.putRetention({ power_days: null });
    await api.restoreApply({ upload_id: 'a'.repeat(32), passphrase: 'p', mode: 'new_home' });
    await api.integrationsForHome('h1');
    await api.devices('h1', { includeArchived: true });
    expect(calls.map(([u, i]) => [i.method, u, i.body ? JSON.parse(i.body as string) : undefined])).toEqual([
      ['POST', '/api/v1/auth/password', { old_password: 'oud', new_password: 'nieuw-wachtwoord', keep_phones: false }],
      ['POST', '/api/v1/pairing/codes', { label: 'Tel', role: 'admin', password: 'geheim' }],
      ['POST', '/api/v1/pairing/codes', { label: 'Oud' }],
      ['PATCH', '/api/v1/users/u2', { disabled: true }],
      ['GET', '/api/v1/audit?limit=50&before=120', undefined],
      ['POST', '/api/v1/devices/d1/unbind', { role: 'airco' }],
      ['PUT', '/api/v1/zones/z1/policy', { watchdog_minutes: 0 }],
      ['PUT', '/api/v1/system/backup-settings', { hour: 4, keep: 10 }],
      ['PUT', '/api/v1/system/retention', { power_days: null }],
      ['POST', '/api/v1/system/restore/apply', { upload_id: 'a'.repeat(32), passphrase: 'p', mode: 'new_home' }],
      ['GET', '/api/v1/integrations?home_id=h1', undefined],
      ['GET', '/api/v1/homes/h1/devices?include_archived=true', undefined],
    ]);
    expect(api.exportUrl('h1', 'csv', '2026-11-01', '2026-11-07', 'day')).toBe('/api/v1/homes/h1/energy/export?format=csv&from=2026-11-01&to=2026-11-07&resolution=day');
  });
});

describe('upload with progress', () => {
  it('posts multipart with CSRF, reports progress and maps errors', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr);
    document.cookie = 'shc_csrf=tok; path=/';
    const seen: number[] = [];
    FakeXhr.next = { status: 200, body: { upload_id: 'x' } };
    const form = new FormData();
    form.append('passphrase', 'p');
    const res = await uploadWithProgress<{ upload_id: string }>('/system/restore/verify', form, { onProgress: (p) => seen.push(p.fraction ?? -1) });
    expect(res.upload_id).toBe('x');
    expect(seen).toEqual([0.5, 1]);
    expect(FakeXhr.last!.headers['X-CSRF-Token']).toBe('tok');
    expect(FakeXhr.last!.url).toBe('/api/v1/system/restore/verify');
    FakeXhr.next = { status: 422, body: { detail: { code: 'backup_invalid', message: 'Wachtwoordzin klopt niet' } } };
    await expect(uploadWithProgress('/system/restore/verify', form)).rejects.toMatchObject({ code: 'backup_invalid', message: 'Wachtwoordzin klopt niet' });
  });
});
