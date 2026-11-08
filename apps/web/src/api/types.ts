// Types mirror docs/api.md (API contract v1). Keep them in sync.

export type Ts = string; // ISO-8601 UTC

export type Role = 'admin' | 'viewer';
export interface User {
  id: string;
  username: string;
  role: Role;
}

export interface AuthStatus {
  setup_required: boolean;
  user: User | null;
}
export interface AuthResult {
  user: User;
  csrf_token: string;
}

export interface ApiErrorBody {
  detail: { code: string; message: string };
}

export interface PairingCode {
  code: string;
  expires_at: Ts;
  qr_payload: string;
}
export interface QrPayload {
  v: number;
  type: 'shc-pair';
  code: string;
  lan: string;
  remote: string | null;
  home: string;
}
export interface Client {
  id: string;
  name: string;
  created_at: Ts;
  last_used_at: Ts | null;
  revoked: boolean;
}

export interface Home {
  id: string;
  name: string;
  timezone: string;
  currency: string;
  active: boolean;
}

export type CheckState = 'ok' | 'warn' | 'error' | 'unknown';
export interface StatusCheck {
  key: string;
  label: string;
  state: CheckState;
  detail: string;
}

export type Severity = 'info' | 'warn' | 'error';
export interface Alert {
  id: string;
  severity: Severity;
  title: string;
  detail: string;
  created_at: Ts;
  acknowledged: boolean;
  source: string;
}

export type Quality = 'measured' | 'derived' | 'estimated' | 'missing';
export interface Measurement {
  value: number | null;
  unit: string;
  quality: Quality;
  at: Ts | null;
  stale: boolean;
}

export type HvacMode = 'off' | 'heat' | 'cool' | 'auto' | 'dry' | 'fan';
export type FanSpeed = 'auto' | 'low' | 'medium' | 'high';
export interface ZonePolicy {
  band_c: number;
  min_interval_s: number;
  max_step_c: number;
  max_starts_per_hour: number;
  stale_after_s: number;
}
export interface Zone {
  id: string;
  name: string;
  room: string;
  online: boolean;
  mode: HvacMode;
  target_c: number | null;
  fan: FanSpeed | null;
  temperature: Measurement;
  humidity: Measurement;
  control: 'schedule' | 'manual' | 'oem_fallback' | 'off';
  profile: string | null;
  override: { until: Ts | null; source: 'app' | 'remote_control' | 'user' } | null;
  capabilities: { modes: HvacMode[]; fan: boolean; swing: boolean; min_c: number; max_c: number; step_c: number };
  blocked_modes: { mode: HvacMode; reason: string }[];
  next_change: { at: Ts; target_c: number; label: string } | null;
  policy: ZonePolicy;
  notice: string | null;
}
export interface ZoneCommand {
  mode?: HvacMode;
  target_c?: number;
  fan?: FanSpeed;
  override_minutes?: number;
}
export interface CommandAccepted {
  command_id: string;
  status: 'queued';
}
export interface ZoneHistory {
  temperature: [Ts, number][];
  target: [Ts, number][];
}
export interface ZoneLogEntry {
  at: Ts;
  kind: string;
  message: string;
}
export interface ScheduleBlock {
  days: number[];
  start: string; // HH:MM
  target_c: number;
  label: string;
}
export interface Schedule {
  blocks: ScheduleBlock[];
}

export interface LightGroup {
  id: string;
  name: string;
  on: boolean;
  brightness: number | null;
  scene: string | null;
  reachable: boolean;
  lights_on: number;
  lights_total: number;
}
export interface Scene {
  id: string;
  name: string;
  group_id: string;
}
export interface Lights {
  groups: LightGroup[];
  scenes: Scene[];
}
export interface ButtonMapping {
  id: string;
  event: string;
  mode: 'lights' | 'climate' | 'combined';
  action: Record<string, unknown>;
  conflict: string | null;
}
export interface HueButton {
  id: string;
  name: string;
  model: string;
  events: string[];
  mapping: ButtonMapping[];
}

export type Period = 'day' | 'week' | 'month' | 'year';
export type MeterRole = 'grid_import' | 'grid_export' | 'airco' | 'boiler' | 'rest' | string;
export interface EnergyMeter {
  role: MeterRole;
  label: string;
  kwh: number | null;
  cost: number | null;
  quality: Quality;
  note: string | null;
}
export interface EnergyBucket {
  start: Ts;
  values: Record<string, number>;
  complete: boolean;
}
export interface EnergySummary {
  period: Period;
  start: Ts;
  end: Ts;
  total_kwh: number | null;
  cost: number | null;
  currency: string;
  meters: EnergyMeter[];
  buckets: EnergyBucket[];
}
export interface LivePower {
  at: Ts;
  total_w: number | null;
  parts: { role: string; label: string; watts: number | null; quality: Quality }[];
}
export interface Tariff {
  id: string;
  valid_from: string;
  import_per_kwh: number;
  export_per_kwh: number;
  fixed_per_day: number | null;
  source: string;
}
export type NewTariff = Omit<Tariff, 'id' | 'source'>;

export interface Overview {
  home: Home;
  status: StatusCheck[];
  zones: Zone[];
  lights: LightGroup[];
  energy_today: EnergySummary;
  power_now: LivePower | null;
  alerts: Alert[];
}

export interface Device {
  id: string;
  provider: string;
  kind: string;
  name: string;
  stable_identity: string;
  room: string | null;
  roles: string[];
  online: boolean;
  last_seen: Ts | null;
  capabilities: string[];
  protected: boolean;
  compatibility: 'tested' | 'untested' | 'unsupported';
}
export interface Integration {
  provider: string;
  label: string;
  state: 'ok' | 'warn' | 'error' | 'not_configured';
  detail: string;
  last_ok_at: Ts | null;
}
export interface DiscoveredDevice {
  provider: string;
  stable_identity: string;
  kind: string;
  name: string;
  host: string | null;
  model: string | null;
  firmware: string | null;
}
export interface PairingInstructions {
  steps: string[];
  retry_after_s: number | null;
}

export interface SystemStatus {
  checks: StatusCheck[];
}
export interface Backup {
  id: string;
  created_at: Ts;
  size_bytes: number | null;
  kind: string;
  status: string;
  checksum: string | null;
}

export type WsMessage =
  | { type: 'zone.updated'; data: Zone }
  | { type: 'lights.updated'; data: LightGroup }
  | { type: 'energy.live'; data: LivePower }
  | { type: 'alert.created'; data: Alert }
  | { type: 'status.updated'; data: StatusCheck[] };
