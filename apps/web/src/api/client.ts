import type * as T from './types';

export const API_BASE = '/api/v1';

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
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

type ReachabilityListener = (reachable: boolean) => void;
const listeners = new Set<ReachabilityListener>();
export function onReachability(fn: ReachabilityListener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
function notify(reachable: boolean) {
  listeners.forEach((fn) => fn(reachable));
}

export function readCookie(name: string): string | null {
  const parts = document.cookie ? document.cookie.split('; ') : [];
  for (const part of parts) {
    const eq = part.indexOf('=');
    if (part.slice(0, eq) === name) return decodeURIComponent(part.slice(eq + 1));
  }
  return null;
}

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

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
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    notify(false);
    throw new NetworkError();
  }
  // A reverse proxy answering 502/503/504 without our JSON body means the API is down.
  if ([502, 503, 504].includes(res.status) && !(res.headers.get('content-type') ?? '').includes('json')) {
    notify(false);
    throw new NetworkError();
  }
  notify(true);
  if (res.status === 204) return undefined as R;
  const text = await res.text();
  const data = text ? JSON.parse(text) : undefined;
  if (!res.ok) {
    const d = (data as T.ApiErrorBody | undefined)?.detail;
    throw new ApiError(res.status, d?.code ?? 'error', d?.message ?? `Fout ${res.status}`);
  }
  return data as R;
}

const get = <R>(p: string) => request<R>('GET', p);
const post = <R>(p: string, b?: unknown) => request<R>('POST', p, b ?? {});
const put = <R>(p: string, b: unknown) => request<R>('PUT', p, b);
const del = <R>(p: string) => request<R>('DELETE', p);

export const api = {
  authStatus: () => get<T.AuthStatus>('/auth/status'),
  setup: (username: string, password: string) => post<T.AuthResult>('/auth/setup', { username, password }),
  login: (username: string, password: string) => post<T.AuthResult>('/auth/login', { username, password }),
  logout: () => post<void>('/auth/logout'),
  me: () => get<T.User>('/auth/me'),

  createPairingCode: (label?: string) => post<T.PairingCode>('/pairing/codes', label ? { label } : {}),
  clients: () => get<T.Client[]>('/clients'),
  revokeClient: (id: string) => del<void>(`/clients/${id}`),

  homes: () => get<T.Home[]>('/homes'),
  overview: (homeId: string) => get<T.Overview>(`/homes/${homeId}/overview`),

  zones: (homeId: string) => get<T.Zone[]>(`/homes/${homeId}/zones`),
  zone: (id: string) => get<T.Zone>(`/zones/${id}`),
  zoneCommand: (id: string, cmd: T.ZoneCommand) => post<T.CommandAccepted>(`/zones/${id}/command`, cmd),
  zoneResume: (id: string) => post<T.Zone>(`/zones/${id}/resume`),
  zoneHistory: (id: string, hours = 24) => get<T.ZoneHistory>(`/zones/${id}/history?hours=${hours}`),
  zoneLog: (id: string, limit = 50) => get<T.ZoneLogEntry[]>(`/zones/${id}/log?limit=${limit}`),
  zoneSchedule: (id: string) => get<T.Schedule>(`/zones/${id}/schedule`),
  putZoneSchedule: (id: string, s: T.Schedule) => put<T.Schedule>(`/zones/${id}/schedule`, s),

  lights: (homeId: string) => get<T.Lights>(`/homes/${homeId}/lights`),
  setGroup: (id: string, body: { on?: boolean; brightness?: number; scene_id?: string }) =>
    post<unknown>(`/lights/groups/${id}`, body),
  buttons: (homeId: string) => get<T.HueButton[]>(`/homes/${homeId}/buttons`),

  energySummary: (homeId: string, period: T.Period, date: string) =>
    get<T.EnergySummary>(`/homes/${homeId}/energy/summary?period=${period}&date=${date}`),
  energyLive: (homeId: string) => get<T.LivePower>(`/homes/${homeId}/energy/live`),
  tariffs: (homeId: string) => get<T.Tariff[]>(`/homes/${homeId}/tariffs`),
  addTariff: (homeId: string, t: T.NewTariff) => post<T.Tariff>(`/homes/${homeId}/tariffs`, t),
  exportUrl: (homeId: string, format: 'csv' | 'json', from?: string, to?: string) => {
    const q = new URLSearchParams({ format });
    if (from) q.set('from', from);
    if (to) q.set('to', to);
    return `${API_BASE}/homes/${homeId}/energy/export?${q}`;
  },

  devices: (homeId: string) => get<T.Device[]>(`/homes/${homeId}/devices`),
  patchDevice: (id: string, body: { name?: string; room_id?: string | null }) => request<T.Device>('PATCH', `/devices/${id}`, body),
  bindDevice: (id: string, role: string) => post<T.Device>(`/devices/${id}/bind`, { role }),
  integrations: () => get<T.Integration[]>('/integrations'),
  rooms: (homeId: string) => get<T.Room[]>(`/homes/${homeId}/rooms`),
  zigbeeSetup: (homeId: string) => post<T.Device>('/integrations/zigbee/setup', { home_id: homeId }),
  zigbeePermitJoin: (homeId: string, seconds = 120) => post<T.PermitJoinResult>('/integrations/zigbee/permit-join', { home_id: homeId, seconds }),
  integrationInstructions: (provider: string) => get<T.IntegrationInstructions>(`/integrations/${provider}/instructions`),
  discover: (provider: string, homeId: string, timeout_s?: number) =>
    post<T.DiscoveredDevice[]>(`/integrations/${provider}/discover`, timeout_s ? { home_id: homeId, timeout_s } : { home_id: homeId }),
  pair: (provider: string, body: T.PairIn) => post<T.Device | T.ActionRequired>(`/integrations/${provider}/pair`, body),

  patchZone: (id: string, body: T.ZoneConfig) => request<T.Zone>('PATCH', `/zones/${id}`, body),
  putZonePolicy: (id: string, p: T.ZonePolicy) => put<T.Zone>(`/zones/${id}/policy`, p),

  adaptive: (zoneId: string) => get<T.AdaptiveStatus>(`/zones/${zoneId}/adaptive`),
  adaptiveFit: (zoneId: string) => post<T.AdaptiveModel>(`/zones/${zoneId}/adaptive/fit`),
  adaptiveActivate: (zoneId: string, modelId: string) => post<T.AdaptiveStatus>(`/zones/${zoneId}/adaptive/activate`, { model_id: modelId }),
  adaptiveDisable: (zoneId: string) => post<T.AdaptiveStatus>(`/zones/${zoneId}/adaptive/disable`),
  experiments: (homeId: string) => get<T.Experiment[]>(`/homes/${homeId}/experiments`),
  createExperiment: (homeId: string, e: T.ExperimentIn) => post<T.Experiment>(`/homes/${homeId}/experiments`, e),
  experimentResult: (id: string) => get<T.ExperimentResult>(`/experiments/${id}`),

  createHome: (h: T.NewHome) => post<T.Home>('/homes', h),
  archiveHome: (id: string) => post<T.Home>(`/homes/${id}/archive`),

  systemStatus: () => get<T.SystemStatus>('/system/status'),
  settings: () => get<T.SystemSettings>('/system/settings'),
  putSettings: (s: { lan_url: string | null; remote_url: string | null }) => put<T.SystemSettings>('/system/settings', s),
  releaseSafeMode: () => post<{ safe_mode: boolean }>('/system/safe-mode/release'),
  backups: () => get<T.Backup[]>('/system/backups'),
  createBackup: (b: T.BackupIn) => post<T.Backup>('/system/backups', b),
  backupDownloadUrl: (id: string) => `${API_BASE}/system/backups/${id}/download`,
  alerts: (homeId: string) => get<T.Alert[]>(`/homes/${homeId}/alerts`),
  ackAlert: (id: string) => post<void>(`/alerts/${id}/ack`),
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
