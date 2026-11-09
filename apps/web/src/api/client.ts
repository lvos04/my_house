import type * as T from './types';

export const API_BASE = '/api/v1';

/** Any answer with an error body. `message` is the server's Dutch text. */
export class ApiError extends Error {
  /** Seconds from a Retry-After header (429 rate_limited, 503 api_unavailable). */
  retryAfter: number | null = null;
  constructor(
    public status: number,
    public code: string,
    message: string,
    /** The whole `detail` object: extra fields such as errors, conflicts, diagnostics. */
    public detail: T.ApiErrorDetail = { code, message },
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** Thrown when the server cannot be reached at all (network down, server off). */
export class NetworkError extends Error {
  constructor(message = 'Server niet bereikbaar') {
    super(message);
    this.name = 'NetworkError';
  }
}

/** The answer was not ours: a Cloudflare Access login redirect or an HTML page (item 38). */
export class AccessLoginRequired extends NetworkError {
  constructor() {
    super('Je sessie bij Cloudflare Access is verlopen. De pagina wordt opnieuw geladen om opnieuw in te loggen.');
    this.name = 'AccessLoginRequired';
  }
}

/** Errors worth retrying automatically: unreachable, or nginx says the API is starting/updating. */
export function isTransientError(err: unknown): boolean {
  if (err instanceof AccessLoginRequired) return false;
  if (err instanceof NetworkError) return true;
  return err instanceof ApiError && err.code === 'api_unavailable';
}
export function isApiError(err: unknown, code?: string): err is ApiError {
  return err instanceof ApiError && (code === undefined || err.code === code);
}
/** Dutch message for any error, for display. */
export function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return 'Er ging iets mis. Probeer het opnieuw.';
}

/* ---------- connection events (consumed by ConnectionProvider in state.tsx) ---------- */

export type ConnectionEvent =
  | { type: 'ok' }
  | { type: 'unreachable' }
  /** nginx 503 api_unavailable: the API starts, migrates or updates. Not "offline". */
  | { type: 'starting'; message: string }
  /** A request got 401: the web session ended (not for wrong passwords at login). */
  | { type: 'auth_required'; code: string; message: string }
  /** Cloudflare Access wants a new login and a reload did not help (loop guard): show this link. */
  | { type: 'access_login'; url: string };

type Listener = (ev: ConnectionEvent) => void;
const listeners = new Set<Listener>();
export function onConnectionEvent(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
export function emitConnectionEvent(ev: ConnectionEvent) {
  listeners.forEach((fn) => fn(ev));
}
/** @deprecated use onConnectionEvent. Called with true on any answer from our API, false when unreachable. */
export function onReachability(fn: (reachable: boolean) => void): () => void {
  return onConnectionEvent((ev) => {
    if (ev.type === 'ok') fn(true);
    else if (ev.type === 'unreachable') fn(false);
  });
}

/* ---------- Cloudflare Access (item 38) ---------- */

export const ACCESS_RELOAD_KEY = 'shc.accessReloadAt';
export const ACCESS_RELOAD_PARAM = '_shc_reload';
const ACCESS_LOOP_MS = 60_000;

/** True when a response to an /api request did not come from our API: a redirect (Cloudflare Access
 * sends one when its session expired) or a non-JSON body such as an HTML login page. */
export function isForeignResponse(res: Pick<Response, 'type' | 'status' | 'redirected' | 'url' | 'headers'>): boolean {
  if (res.type === 'opaqueredirect' || (res.status >= 300 && res.status < 400)) return true;
  if (res.redirected && res.url) {
    try {
      if (!new URL(res.url, 'http://x').pathname.startsWith('/api/')) return true;
    } catch {
      return true;
    }
  }
  if (res.status === 204 || res.status === 0) return false;
  const ct = (res.headers.get('content-type') ?? '').toLowerCase();
  // Our API and nginx's own answers are always JSON; an HTML page means someone else answered (a body
  // that is not JSON at all is caught when parsing).
  return ct.includes('text/html') || ct.includes('xml');
}

export function accessLoginUrl(loc: Pick<Location, 'href'> = window.location): string {
  const back = new URL(loc.href);
  back.searchParams.delete(ACCESS_RELOAD_PARAM);
  return `/cdn-cgi/access/login?redirect_url=${encodeURIComponent(back.toString())}`;
}

interface KV {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
}
/** Reload the page once, bypassing caches (cache-busting parameter; navigations are network-first in
 * the service worker), so Cloudflare Access can show its login. A second hit within a minute is a loop:
 * then return 'loop' and let the UI offer the Access login link instead. */
export function reloadForAccess(
  loc: Pick<Location, 'href' | 'replace'> = window.location,
  store: KV | null = safeSession(),
  now: number = Date.now(),
): 'reloaded' | 'loop' {
  try {
    const last = Number(store?.getItem(ACCESS_RELOAD_KEY) ?? 0);
    if (last && now - last < ACCESS_LOOP_MS) return 'loop';
    store?.setItem(ACCESS_RELOAD_KEY, String(now));
  } catch {
    /* storage blocked: still try once */
  }
  const url = new URL(loc.href);
  url.searchParams.set(ACCESS_RELOAD_PARAM, String(now));
  loc.replace(url.toString());
  return 'reloaded';
}
/** Remove the cache-busting parameter after the reload (call once at startup). */
export function stripReloadParam(loc: Location = window.location, hist: History = window.history) {
  try {
    const url = new URL(loc.href);
    if (!url.searchParams.has(ACCESS_RELOAD_PARAM)) return;
    url.searchParams.delete(ACCESS_RELOAD_PARAM);
    hist.replaceState(hist.state, '', url.pathname + url.search + url.hash);
  } catch {
    /* ignore */
  }
}
function safeSession(): KV | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

let accessHandler: () => void = () => {
  if (reloadForAccess() === 'loop') emitConnectionEvent({ type: 'access_login', url: accessLoginUrl() });
};
/** Replace what happens on a foreign answer (tests). Returns the previous handler. */
export function setAccessHandler(fn: () => void): () => void {
  const prev = accessHandler;
  accessHandler = fn;
  return prev;
}
function foreignAnswer(): never {
  emitConnectionEvent({ type: 'unreachable' });
  accessHandler();
  throw new AccessLoginRequired();
}

/* ---------- request ---------- */

export function readCookie(name: string): string | null {
  const parts = document.cookie ? document.cookie.split('; ') : [];
  for (const part of parts) {
    const eq = part.indexOf('=');
    if (part.slice(0, eq) === name) return decodeURIComponent(part.slice(eq + 1));
  }
  return null;
}

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
/** 401 on these paths is a normal answer (wrong password), not "session ended". */
const AUTH_FORM_PATHS = ['/auth/login', '/auth/setup'];

function retryAfterOf(h: Headers | null | undefined): number | null {
  const v = h?.get('retry-after');
  if (!v) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function errorFrom(status: number, data: unknown, path: string, retryAfter: number | null): ApiError {
  const d = (data as T.ApiErrorBody | undefined)?.detail;
  const detail: T.ApiErrorDetail =
    d && typeof d === 'object' ? d : { code: 'error', message: typeof d === 'string' ? d : `Fout ${status}` };
  const err = new ApiError(status, detail.code ?? 'error', detail.message ?? `Fout ${status}`, detail);
  err.retryAfter = retryAfter;
  if (status === 401 && !AUTH_FORM_PATHS.includes(path)) {
    emitConnectionEvent({ type: 'auth_required', code: err.code, message: err.message });
  }
  if (status === 503 && err.code === 'api_unavailable') emitConnectionEvent({ type: 'starting', message: err.message });
  return err;
}

export async function request<R>(method: string, path: string, body?: unknown): Promise<R> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (MUTATING.has(method)) {
    const csrf = readCookie('shc_csrf');
    if (csrf) headers['X-CSRF-Token'] = csrf;
  }
  let res: Response;
  try {
    res = await fetch(API_BASE + path, {
      method,
      headers,
      credentials: 'same-origin',
      // A Cloudflare Access login redirect must be seen, not followed cross-origin (that fails as a
      // CORS error and looked like "server niet bereikbaar" forever).
      redirect: 'manual',
      cache: 'no-store',
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    emitConnectionEvent({ type: 'unreachable' });
    throw new NetworkError();
  }
  const ct = (res.headers.get('content-type') ?? '').toLowerCase();
  // A reverse proxy answering 502/503/504 without our JSON body means the API is down.
  if ([502, 503, 504].includes(res.status) && !ct.includes('json')) {
    emitConnectionEvent({ type: 'unreachable' });
    throw new NetworkError();
  }
  if (isForeignResponse(res)) foreignAnswer();
  const text = res.status === 204 ? '' : await res.text();
  let data: unknown = undefined;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      foreignAnswer();
    }
  }
  if (!res.ok) {
    const err = errorFrom(res.status, data, path, retryAfterOf(res.headers));
    if (err.code !== 'api_unavailable') emitConnectionEvent({ type: 'ok' });
    throw err;
  }
  emitConnectionEvent({ type: 'ok' });
  return data as R;
}

/* ---------- upload with progress (restore) ---------- */

export interface UploadProgress {
  loaded: number;
  total: number;
  /** 0..1, or null when the size is unknown. */
  fraction: number | null;
}
export interface UploadOptions {
  onProgress?: (p: UploadProgress) => void;
  signal?: AbortSignal;
  /** Extra headers (e.g. X-Backup-Passphrase). */
  headers?: Record<string, string>;
}

/** POST multipart/form-data with upload progress (XMLHttpRequest; fetch has no upload progress).
 * Errors map exactly like request(): ApiError / NetworkError / AccessLoginRequired; abort rejects with
 * a DOMException named "AbortError". */
export function uploadWithProgress<R>(path: string, form: FormData, opts: UploadOptions = {}): Promise<R> {
  return new Promise<R>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', API_BASE + path);
    xhr.setRequestHeader('Accept', 'application/json');
    const csrf = readCookie('shc_csrf');
    if (csrf) xhr.setRequestHeader('X-CSRF-Token', csrf);
    for (const [k, v] of Object.entries(opts.headers ?? {})) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => {
      opts.onProgress?.({ loaded: e.loaded, total: e.total, fraction: e.lengthComputable && e.total ? e.loaded / e.total : null });
    };
    const onAbort = () => xhr.abort();
    opts.signal?.addEventListener('abort', onAbort, { once: true });
    const done = () => opts.signal?.removeEventListener('abort', onAbort);
    xhr.onerror = () => {
      done();
      emitConnectionEvent({ type: 'unreachable' });
      reject(new NetworkError());
    };
    xhr.onabort = () => {
      done();
      reject(new DOMException('Upload afgebroken', 'AbortError'));
    };
    xhr.onload = () => {
      done();
      const ct = (xhr.getResponseHeader('content-type') ?? '').toLowerCase();
      if ([502, 503, 504].includes(xhr.status) && !ct.includes('json')) {
        emitConnectionEvent({ type: 'unreachable' });
        return reject(new NetworkError());
      }
      let foreign = ct.includes('text/html') || ct.includes('xml');
      try {
        if (xhr.responseURL && !new URL(xhr.responseURL, 'http://x').pathname.startsWith('/api/')) foreign = true;
      } catch {
        /* ignore */
      }
      let data: unknown = undefined;
      if (!foreign && xhr.responseText) {
        try {
          data = JSON.parse(xhr.responseText);
        } catch {
          foreign = true;
        }
      }
      if (foreign) {
        try {
          foreignAnswer();
        } catch (e) {
          return reject(e);
        }
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        const h = new Headers();
        const ra = xhr.getResponseHeader('retry-after');
        if (ra) h.set('retry-after', ra);
        return reject(errorFrom(xhr.status, data, path, retryAfterOf(h)));
      }
      emitConnectionEvent({ type: 'ok' });
      resolve(data as R);
    };
    xhr.send(form);
  });
}

/* ---------- endpoints ---------- */

const get = <R>(p: string) => request<R>('GET', p);
const post = <R>(p: string, b?: unknown) => request<R>('POST', p, b ?? {});
const put = <R>(p: string, b: unknown) => request<R>('PUT', p, b);
const patch = <R>(p: string, b: unknown) => request<R>('PATCH', p, b);
const del = <R>(p: string) => request<R>('DELETE', p);
const enc = encodeURIComponent;

function query(params: Record<string, unknown>): string {
  const q = new URLSearchParams();
  // Only scalars: a React Query context passed by `queryFn: api.x` by mistake is ignored.
  for (const [k, v] of Object.entries(params))
    if ((typeof v === 'string' && v !== '') || typeof v === 'number' || typeof v === 'boolean') q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : '';
}

/** URL of a streamed energy export (use as href with `download`; disable while offline). */
export function exportHref(homeId: string, o: T.ExportOptions): string {
  return `${API_BASE}/homes/${homeId}/energy/export${query({ format: o.format, from: o.from, to: o.to, resolution: o.resolution })}`;
}

export const api = {
  /* auth & account */
  authStatus: () => get<T.AuthStatus>('/auth/status'),
  setup: (username: string, password: string) => post<T.AuthResult>('/auth/setup', { username, password }),
  login: (username: string, password: string) => post<T.AuthResult>('/auth/login', { username, password }),
  logout: () => post<void>('/auth/logout'),
  me: () => get<T.Me>('/auth/me'),
  /** Own password. 403 wrong_password; 422 too short / same as old. Other sessions end; phones too unless keep_phones. */
  changePassword: (b: T.PasswordChangeIn) => post<T.AccessRevoked>('/auth/password', b),

  /* users (admin) */
  users: () => get<T.UserFull[]>('/users'),
  /** 409 username_taken; 422 password < 12. */
  createUser: (u: T.NewUser) => post<T.UserFull>('/users', u),
  /** 409 self_change / last_admin. Disabling ends the user's sessions and phones. */
  patchUser: (id: string, b: T.UserPatch) => patch<T.UserFull>(`/users/${id}`, b),
  /** Sets a new password and always revokes that user's sessions and phones. */
  resetUserPassword: (id: string, password: string) => post<T.AccessRevoked>(`/users/${id}/password`, { password }),
  /** 409 self_delete / last_admin. */
  deleteUser: (id: string) => del<void>(`/users/${id}`),
  audit: (q: T.AuditQuery = {}) => get<T.AuditEntry[]>(`/audit${query({ limit: q.limit, before: q.before, actor: q.actor, action: q.action })}`),

  /* pairing & phones */
  serverIdentity: () => get<T.ServerIdentity>('/pairing/identity'),
  /** Admin role needs `password` (403 password_required / wrong_password). 409 lan_url_required / lan_url_invalid / remote_url_invalid. */
  createPairingCode: (arg: string | T.PairingCodeIn = {}) =>
    post<T.PairingCode>('/pairing/codes', typeof arg === 'string' ? (arg ? { label: arg } : {}) : arg),
  clients: () => get<T.Client[]>('/clients'),
  revokeClient: (id: string) => del<void>(`/clients/${id}`),
  clientSelf: () => get<T.Client>('/clients/self'),
  refreshClientSelf: () => post<T.TokenRefresh>('/clients/self/refresh'),
  unpairSelf: () => del<void>('/clients/self'),

  /* homes */
  homes: () => get<T.Home[]>('/homes'),
  createHome: (h: T.NewHome) => post<T.Home>('/homes', h),
  archiveHome: (id: string) => post<T.Home>(`/homes/${id}/archive`),
  overview: (homeId: string) => get<T.Overview>(`/homes/${homeId}/overview`),
  rooms: (homeId: string) => get<T.Room[]>(`/homes/${homeId}/rooms`),
  alerts: (homeId: string) => get<T.Alert[]>(`/homes/${homeId}/alerts`),
  ackAlert: (id: string) => post<T.Alert>(`/alerts/${id}/ack`),

  /* status */
  health: () => get<T.Health>('/health'),
  systemStatus: () => get<T.SystemStatus>('/system/status'),
  preflight: () => get<T.Preflight>('/system/preflight'),

  /* climate */
  zones: (homeId: string) => get<T.Zone[]>(`/homes/${homeId}/zones`),
  zone: (id: string) => get<T.Zone>(`/zones/${id}`),
  /** 202 {command_id}. Track it with trackCommand()/useCommand(). 409 mode_required / policy_denied / unsupported. */
  zoneCommand: (id: string, cmd: T.ZoneCommand) => post<T.CommandAccepted>(`/zones/${id}/command`, cmd),
  zoneResume: (id: string) => post<T.Zone>(`/zones/${id}/resume`),
  zoneHistory: (id: string, hours = 24) => get<T.ZoneHistory>(`/zones/${id}/history?hours=${hours}`),
  zoneLog: (id: string, limit = 50) => get<T.ZoneLogEntry[]>(`/zones/${id}/log?limit=${limit}`),
  zoneSchedule: (id: string) => get<T.Schedule>(`/zones/${id}/schedule`),
  /** Detaches the zone from its profile. 422 with a Dutch message for invalid blocks. */
  putZoneSchedule: (id: string, s: T.ScheduleIn) => put<T.Schedule>(`/zones/${id}/schedule`, { blocks: s.blocks }),
  /** Partial: only the fields sent change. */
  putZonePolicy: (id: string, p: T.ZonePolicyIn) => put<T.Zone>(`/zones/${id}/policy`, p),
  patchZone: (id: string, body: T.ZoneConfig) => patch<T.Zone>(`/zones/${id}`, body),
  climateRoles: (homeId: string) => get<T.ClimateRole[]>(`/homes/${homeId}/climate/roles`),

  /* comfort profiles */
  profiles: (homeId: string) => get<T.Profile[]>(`/homes/${homeId}/profiles`),
  profile: (id: string) => get<T.Profile>(`/profiles/${id}`),
  /** 409 conflict on a duplicate name. */
  createProfile: (homeId: string, p: T.ProfileIn) => post<T.Profile>(`/homes/${homeId}/profiles`, p),
  updateProfile: (id: string, p: T.ProfileIn) => put<T.Profile>(`/profiles/${id}`, p),
  /** 409 while an experiment uses it. */
  deleteProfile: (id: string) => del<void>(`/profiles/${id}`),
  applyProfile: (zoneId: string, profileId: string) => post<T.Zone>(`/zones/${zoneId}/profile`, { profile_id: profileId }),

  /* optimisation */
  adaptive: (zoneId: string) => get<T.AdaptiveStatus>(`/zones/${zoneId}/adaptive`),
  adaptiveFit: (zoneId: string) => post<T.AdaptiveModel>(`/zones/${zoneId}/adaptive/fit`),
  adaptiveActivate: (zoneId: string, modelId: string) => post<T.AdaptiveStatus>(`/zones/${zoneId}/adaptive/activate`, { model_id: modelId }),
  adaptiveDisable: (zoneId: string) => post<T.AdaptiveStatus>(`/zones/${zoneId}/adaptive/disable`),
  experiments: (homeId: string) => get<T.Experiment[]>(`/homes/${homeId}/experiments`),
  createExperiment: (homeId: string, e: T.ExperimentIn) => post<T.Experiment>(`/homes/${homeId}/experiments`, e),
  experimentResult: (id: string) => get<T.ExperimentResult>(`/experiments/${id}`),
  pauseExperiment: (id: string) => post<T.Experiment>(`/experiments/${id}/pause`),
  /** 409 while the zone is manual/off. */
  resumeExperiment: (id: string) => post<T.Experiment>(`/experiments/${id}/resume`),
  /** Stop for good. By default (restore) an auto-apply experiment gives the zone back the profile and
   * schedule it had before; `restore: false` keeps the variant's profile. `zone_restored` says what happened. */
  stopExperiment: (id: string, opts: { restore?: boolean } = {}) =>
    post<T.ExperimentStopped>(`/experiments/${id}/stop${opts.restore === false ? '?restore=false' : ''}`),
  /** Same choice as stopExperiment for the zone; the experiment and its results are removed. */
  deleteExperiment: (id: string, opts: { restore?: boolean } = {}) => del<void>(`/experiments/${id}${opts.restore === false ? '?restore=false' : ''}`),

  /* lights & buttons */
  lights: (homeId: string) => get<T.Lights>(`/homes/${homeId}/lights`),
  /** 202 {command_id}. Track it with trackCommand()/useCommand(). */
  setGroup: (id: string, body: T.GroupCommand) => post<T.CommandAccepted>(`/lights/groups/${id}`, body),
  buttons: (homeId: string) => get<T.HueButton[]>(`/homes/${homeId}/buttons`),
  button: (id: string) => get<T.HueButton>(`/buttons/${id}`),
  /** Bulk replace. 422 validation_error (detail.errors) / 409 conflict (detail.conflicts). */
  putButtonMappings: (id: string, mappings: T.ButtonMappingIn[]) => put<T.HueButton>(`/buttons/${id}/mappings`, mappings),
  addButtonMapping: (id: string, m: T.ButtonMappingIn) => post<T.HueButton>(`/buttons/${id}/mappings`, m),
  patchButtonMapping: (mappingId: string, m: T.ButtonMappingPatch) => patch<T.HueButton>(`/button-mappings/${mappingId}`, m),
  deleteButtonMapping: (mappingId: string) => del<void>(`/button-mappings/${mappingId}`),
  /** What a press would do now. Executes nothing. */
  simulateButton: (id: string, button: string, event: string) => post<T.SimulateResult>(`/buttons/${id}/simulate`, { button, event }),

  /* energy */
  energySummary: (homeId: string, period: T.Period, date: string) =>
    get<T.EnergySummary>(`/homes/${homeId}/energy/summary?period=${period}&date=${date}`),
  energyLive: (homeId: string) => get<T.LivePower | null>(`/homes/${homeId}/energy/live`),
  tariffs: (homeId: string) => get<T.Tariff[]>(`/homes/${homeId}/tariffs`),
  addTariff: (homeId: string, t: T.TariffIn) => post<T.Tariff>(`/homes/${homeId}/tariffs`, t),
  updateTariff: (id: string, t: T.TariffIn) => put<T.Tariff>(`/tariffs/${id}`, t),
  deleteTariff: (id: string) => del<void>(`/tariffs/${id}`),
  energyLimits: () => get<T.EnergyLimits>('/energy/limits'),
  putEnergyLimits: (l: T.EnergyLimitsIn) => put<T.EnergyLimits>('/energy/limits', l),
  exportUrl: (homeId: string, format: T.ExportFormat, from?: string, to?: string, resolution?: T.ExportResolution) =>
    exportHref(homeId, { format, from, to, resolution }),

  /* devices */
  devices: (homeId: string, opts: { includeArchived?: boolean } = {}) =>
    get<T.Device[]>(`/homes/${homeId}/devices${opts.includeArchived ? '?include_archived=true' : ''}`),
  device: (id: string) => get<T.Device>(`/devices/${id}`),
  patchDevice: (id: string, body: T.DevicePatch) => patch<T.Device>(`/devices/${id}`, body),
  /** zigbee_sensor and ac_unit only (409 unsupported otherwise). */
  calibrateDevice: (id: string, body: T.CalibrationIn) => patch<T.Device>(`/devices/${id}/calibration`, body),
  /** 409 role_mismatch (kind does not fit) / archived. */
  bindDevice: (id: string, role: string) => post<T.Device>(`/devices/${id}/bind`, { role }),
  /** 409 not_bound. History is kept. */
  unbindDevice: (id: string, role: string) => post<T.Device>(`/devices/${id}/unbind`, { role }),
  /** DELETE = archive: roles closed, zone links cleared, queued commands cancelled. Readings stay. */
  archiveDevice: (id: string) => del<T.Device>(`/devices/${id}`),
  restoreDevice: (id: string) => patch<T.Device>(`/devices/${id}`, { lifecycle: 'active' }),
  replaceDevice: (id: string, newDeviceId: string) => post<T.ReplaceResult>(`/devices/${id}/replace`, { new_device_id: newDeviceId }),
  /** Read-only check, never switches anything. */
  testDevice: (id: string) => post<T.DeviceTest>(`/devices/${id}/test`),
  markTested: (id: string, note?: string) => post<T.Device>(`/devices/${id}/tested`, note ? { note } : {}),
  deviceDiagnostics: (id: string) => get<T.DeviceDiagnostics>(`/devices/${id}/diagnostics`),

  /* integrations */
  /** All integrations, plus "not configured" rows for providers without one. Zero-arg: safe as queryFn. */
  integrations: () => get<T.Integration[]>('/integrations'),
  integrationsForHome: (homeId: string) => get<T.Integration[]>(`/integrations${query({ home_id: homeId })}`),
  integration: (id: string) => get<T.IntegrationDetail>(`/integrations/${id}`),
  /** Address/name change. With verify (default) 409 identity_mismatch or 502 when unreachable. */
  patchIntegration: (id: string, body: T.IntegrationPatch) => patch<T.IntegrationDetail>(`/integrations/${id}`, body),
  removeIntegration: (id: string) => del<void>(`/integrations/${id}`),
  compatibility: () => get<T.CompatibilityMatrix>('/integrations/compatibility'),
  integrationInstructions: (provider: string) => get<T.IntegrationInstructions>(`/integrations/${enc(provider)}/instructions`),
  discover: (provider: string, homeId: string, timeout_s?: number) =>
    post<T.DiscoveredDevice[]>(`/integrations/${enc(provider)}/discover`, timeout_s ? { home_id: homeId, timeout_s } : { home_id: homeId }),
  /** PairResult, or 202 ActionRequired (press the link button, then call again). Errors carry detail.diagnostics. */
  pair: (provider: string, body: T.PairIn) => post<T.PairResult | T.ActionRequired>(`/integrations/${enc(provider)}/pair`, body),
  zigbeeSetup: (homeId: string) => post<T.Device>('/integrations/zigbee/setup', { home_id: homeId }),
  zigbeePermitJoin: (homeId: string, seconds = 120) => post<T.PermitJoinResult>('/integrations/zigbee/permit-join', { home_id: homeId, seconds }),

  /* system */
  settings: () => get<T.SystemSettings>('/system/settings'),
  /** 422 validation_error with a Dutch message for invalid addresses. */
  putSettings: (s: T.AddressesIn) => put<T.SystemSettings>('/system/settings', s),
  releaseSafeMode: () => post<{ safe_mode: boolean }>('/system/safe-mode/release'),
  backups: () => get<T.Backup[]>('/system/backups'),
  /** 200 with the finished backup, or 202 with status "running" (poll backups()). 500 backup_failed. */
  createBackup: (b: T.BackupIn) => post<T.Backup>('/system/backups', b),
  backupDownloadUrl: (id: string) => `${API_BASE}/system/backups/${id}/download`,
  backupSettings: () => get<T.BackupSettings>('/system/backup-settings'),
  putBackupSettings: (b: T.BackupSettingsIn) => put<T.BackupSettings>('/system/backup-settings', b),
  /** Push outside the app (web session only). */
  notify: () => get<T.NotifySettings>('/system/notify'),
  /** 422 validation_error with a Dutch reason for an unusable address; null removes the address set in the app. */
  putNotify: (url: string | null) => put<T.NotifySettings>('/system/notify', { url } satisfies T.NotifyIn),
  /** Sends a test message: {ok, error}. 409 not_configured when no address is set. */
  testNotify: () => post<T.NotifyTestResult>('/system/notify/test'),
  retention: () => get<T.RetentionSettings>('/system/retention'),
  /** 422 with a Dutch message when below the minimum or above the maximum. */
  putRetention: (r: T.RetentionIn) => put<T.RetentionSettings>('/system/retention', r),
  /** Upload + verify (nothing changes yet). 422 backup_invalid (wrong passphrase / corrupt), 413, 507 disk_full. */
  restoreVerify: (file: Blob, passphrase: string, opts: UploadOptions & { filename?: string } = {}) => {
    const form = new FormData();
    form.append('passphrase', passphrase);
    form.append('file', file, opts.filename ?? (file instanceof File ? file.name : 'backup.shcbak'));
    return uploadWithProgress<T.RestoreVerify>('/system/restore/verify', form, opts);
  },
  restoreDiscard: (uploadId: string) => del<void>(`/system/restore/uploads/${enc(uploadId)}`),
  /** 202 {job_id}. 409 restore_running / backup_running / restore_refused; 422 backup_invalid. */
  restoreApply: (b: T.RestoreApplyIn) => post<T.RestoreApplyResult>('/system/restore/apply', b),
  /** No login needed (sessions are replaced by the restore). */
  restoreJob: (jobId: string) => get<T.RestoreJob>(`/system/restore/jobs/${enc(jobId)}`),
  restoreCompare: (homeId?: string) => get<T.RestoreCompare>(`/system/restore/compare${query({ home_id: homeId })}`),
};

export function isActionRequired(x: unknown): x is T.ActionRequired {
  return typeof x === 'object' && x !== null && (x as { status?: string }).status === 'action_required';
}

export function parseQrPayload(raw: string): T.QrPayload | null {
  try {
    const p = JSON.parse(raw) as T.QrPayload;
    if (p && p.type === 'shc-pair' && typeof p.code === 'string') return p;
  } catch {
    /* ignore */
  }
  return null;
}
