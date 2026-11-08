import { describe, expect, it, vi, afterEach } from 'vitest';
import { api, ApiError, NetworkError, onReachability, parseQrPayload, readCookie } from '../api/client';

function mockFetch(status = 200, body: unknown = {}) {
  const fn = vi.fn(async () => new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }));
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe('api client', () => {
  it('sends X-CSRF-Token from the shc_csrf cookie on mutations', async () => {
    document.cookie = 'shc_csrf=abc%20123; path=/';
    const fn = mockFetch(202, { command_id: 'c1', status: 'queued' });
    await api.zoneCommand('z1', { target_c: 21.5 });
    const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/v1/zones/z1/command');
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('abc 123');
    expect(init.credentials).toBe('same-origin');
    expect(JSON.parse(init.body as string)).toEqual({ target_c: 21.5 });
  });

  it('does not send the CSRF header on GET', async () => {
    document.cookie = 'shc_csrf=abc; path=/';
    const fn = mockFetch(200, []);
    await api.homes();
    const init = (fn.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBeUndefined();
  });

  it('maps the error body to ApiError with the Dutch message', async () => {
    mockFetch(409, { detail: { code: 'policy_denied', message: 'Koelen kan nu niet' } });
    await expect(api.zoneCommand('z1', { mode: 'cool' })).rejects.toMatchObject({ status: 409, code: 'policy_denied', message: 'Koelen kan nu niet' });
    await expect(api.zoneCommand('z1', { mode: 'cool' })).rejects.toBeInstanceOf(ApiError);
  });

  it('reports unreachable server as NetworkError and notifies listeners', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    const seen: boolean[] = [];
    const off = onReachability((ok) => seen.push(ok));
    await expect(api.homes()).rejects.toBeInstanceOf(NetworkError);
    off();
    expect(seen).toEqual([false]);
  });

  it('reads cookies and parses QR payloads', () => {
    document.cookie = 'other=1; path=/';
    expect(readCookie('missing')).toBeNull();
    expect(parseQrPayload('{"v":1,"type":"shc-pair","code":"K7Q4-9XPM","lan":"http://x","remote":null,"home":"Thuis"}')?.code).toBe('K7Q4-9XPM');
    expect(parseQrPayload('nonsense')).toBeNull();
    expect(parseQrPayload('{"type":"other"}')).toBeNull();
  });
});
