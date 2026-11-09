// API contract v1. Mirrors the backend (apps/api/src/shc_api/routers/*.py, views/*.py), which is the
// source of truth. Response types are exact; *In types are request bodies (lenient where the API is).

export type Ts = string; // ISO-8601 UTC
export type Uuid = string;

/* ------------------------------------------------------------------ errors */

/** `{"detail": {...}}` body of every API error (also nginx's own 503/413/429 answers). */
export interface ApiErrorDetail {
  code: string;
  message: string;
  /** 422 button mappings: every validation problem. */
  errors?: string[];
  /** 409 button mappings: blocking conflicts per mapping index. */
  conflicts?: { index: number; message: string }[];
  /** Failed pairing: what the adapter found (no secrets), plus the records it saved. */
  diagnostics?: Record<string, unknown>;
  integration_id?: Uuid;
  device_id?: Uuid | null;
  [extra: string]: unknown;
}
export interface ApiErrorBody {
  detail: ApiErrorDetail;
}

/** Error codes the UI reacts to. Messages are always Dutch and come from the server. */
export type ApiErrorCode =
  | 'auth_required'
  | 'forbidden'
  | 'not_found'
  | 'validation_error'
  | 'conflict'
  | 'policy_denied'
  | 'mode_required'
  | 'unsupported'
  | 'rate_limited'
  | 'payload_too_large'
  | 'request_too_large'
  | 'api_unavailable'
  | 'token_revoked'
  | 'token_expired'
  | 'wrong_password'
  | 'password_required'
  | 'username_taken'
  | 'last_admin'
  | 'self_change'
  | 'self_delete'
  | 'lan_url_required'
  | 'lan_url_invalid'
  | 'remote_url_invalid'
  | 'role_mismatch'
  | 'not_bound'
  | 'archived'
  | 'identity_mismatch'
  | 'certificate_mismatch'
  | 'device_offline'
  | 'pair_failed'
  | 'discovery_failed'
  | 'not_configured'
  | 'tariff_exists'
  | 'range_too_large'
  | 'insufficient_data'
  | 'backup_failed'
  | 'backup_invalid'
  | 'disk_full'
  | 'restore_running'
  | 'backup_running'
  | 'restore_refused'
  | 'setup_done'
  | 'invalid_code'
  | (string & {});

/* ------------------------------------------------------------------ auth & users */

export type Role = 'admin' | 'viewer';
/** Role of a paired phone. Web sessions have client_role null. */
export type ClientRole = 'admin' | 'control' | 'viewer';

/** User as returned by /auth/status, /auth/login and the WS hello. Fields after `role` come from
 * /auth/me and are present once the app has loaded it (see useCan()). */
export interface User {
  id: Uuid;
  username: string;
  role: Role;
  via?: 'cookie' | 'token';
  client_role?: ClientRole | null;
  is_admin?: boolean;
  can_control?: boolean;
  password_changed_at?: Ts | null;
}
/** GET /auth/me */
export interface Me extends User {
  via: 'cookie' | 'token';
  client_role: ClientRole | null;
  is_admin: boolean;
  can_control: boolean;
  password_changed_at: Ts | null;
}
export interface AuthStatus {
  setup_required: boolean;
  user: User | null;
}
export interface AuthResult {
  user: User;
  csrf_token: string;
}
/** POST /auth/password */
export interface PasswordChangeIn {
  old_password: string;
  new_password: string;
  /** true = keep this user's paired phones signed in. */
  keep_phones?: boolean;
}
/** Result of a password change/reset or disabling a user. */
export interface AccessRevoked {
  sessions_ended: number;
  phones_revoked: number;
}

/** GET /users (admin). */
export interface UserFull {
  id: Uuid;
  username: string;
  role: Role;
  disabled: boolean;
  created_at: Ts;
  password_changed_at: Ts | null;
  failed_logins: number;
  locked_until: Ts | null;
  locked: boolean;
  active_sessions: number;
  active_phones: number;
}
export interface NewUser {
  username: string;
  password: string;
  role: Role;
}
export interface UserPatch {
  role?: Role;
  disabled?: boolean;
  /** Clear a lockout after too many failed logins. */
  unlock?: boolean;
}
/** GET /audit (admin), newest first. */
export interface AuditEntry {
  id: number;
  at: Ts;
  actor: string;
  action: string;
  target: string | null;
  detail: Record<string, unknown> | null;
}
export interface AuditQuery {
  limit?: number; // 1..500, default 100
  /** Next page: id of the last row of the previous page. */
  before?: number;
  actor?: string;
  action?: string;
}

/* ------------------------------------------------------------------ pairing & phones */

export interface PairingCodeIn {
  label?: string;
  role?: ClientRole; // default "control"
  /** Required for role "admin": the creator's own password. */
  password?: string;
}
export interface PairingCode {
  code: string;
  expires_at: Ts;
  role: ClientRole;
  server_id: Uuid;
  qr_payload: string;
}
export interface QrPayload {
  v: number;
  type: 'shc-pair';
  code: string;
  lan: string;
  remote: string | null;
  home: string | null;
  server_id?: Uuid;
  role?: ClientRole;
}
/** GET /clients (admin): a paired phone. */
export interface Client {
  id: Uuid;
  name: string;
  role: ClientRole;
  created_at: Ts;
  last_used_at: Ts | null;
  expires_at: Ts | null;
  refreshed_at: Ts | null;
  revoked: boolean;
  expired: boolean;
  /** Username of the account the phone belongs to. */
  user?: string;
}
/** GET /pairing/identity (no login). */
export interface ServerIdentity {
  server_id: Uuid;
  name: string | null;
}
/** POST /pairing/redeem (phones). */
export interface RedeemResult {
  token: string;
  user: User;
  client: Client;
  server: { server_id: Uuid; lan_url: string | null; remote_url: string | null; home_id: Uuid | null; home_name: string | null };
}
export interface TokenRefresh {
  token: string;
  expires_at: Ts;
}

/* ------------------------------------------------------------------ homes */

export interface Home {
  id: Uuid;
  name: string;
  timezone: string;
  currency: string;
  active: boolean;
}
export interface NewHome {
  name: string;
  timezone: string;
  currency?: string;
}
export interface Room {
  id: Uuid;
  name: string;
  function: string | null;
}

/* ------------------------------------------------------------------ status & alerts */

export type CheckState = 'ok' | 'warn' | 'error' | 'unknown';

/** Health details of an integration check (GET /system/status only). */
export interface IntegrationCheckDetails {
  code: string | null;
  since: Ts | null;
  health_state: string | null;
  health_detail: string | null;
  checked_at: Ts | null;
  connected_since: Ts | null;
  missed_polls: number;
  consecutive_failures: number;
  /** Adapter specific (Zigbee: mqtt_connected, bridge_online, coordinator_type, devices, ...). */
  extra: Record<string, unknown>;
}
/**
 * One status line. Keys: "worker" (age_s), "safe_mode", "backup" (age_h), "integration:<id>"
 * (integration_id, provider, code), and on /system/status also "database", "worker_loops", "disk",
 * "time", "maintenance", "integration:<id>:mqtt", "integration:<id>:dongle" (Zigbee).
 */
export interface StatusCheck {
  key: string;
  label: string;
  state: CheckState;
  detail: string;
  age_s?: number | null;
  age_h?: number | null;
  integration_id?: Uuid;
  provider?: string;
  code?: string | null;
  details?: IntegrationCheckDetails;
  /** key "backup": versions of the last good backup, Zigbee data in it, automatic backup failures. */
  app_version?: string | null;
  schema_revision?: string | null;
  zigbee2mqtt_included?: boolean | null;
  auto_failures?: number;
  auto_last_error?: string | null;
  /** key "restore": report of the last restore. */
  report?: RestoreReport;
}
export interface SystemStatus {
  checks: StatusCheck[];
  /** App version of the server (VERSION file). */
  version: string;
}
/** GET /health (no login). */
export interface Health {
  status: 'ok';
  version: string;
}
/** GET /system/preflight (admin). Keys: home, worker, mqtt + zigbee_dongle (or zigbee), discovery, clock, lan_url, backup. */
export interface Preflight {
  checked_at: Ts;
  ready: boolean;
  checks: StatusCheck[];
}

export type Severity = 'info' | 'warn' | 'error';
export interface Alert {
  id: Uuid;
  severity: Severity;
  title: string;
  detail: string;
  created_at: Ts;
  acknowledged: boolean;
  source: string;
}

/* ------------------------------------------------------------------ measurements */

export type Quality = 'measured' | 'derived' | 'estimated' | 'missing';
export interface Measurement {
  value: number | null;
  unit: string;
  quality: Quality;
  at: Ts | null;
  stale: boolean;
}

/* ------------------------------------------------------------------ commands */

/** Status of a queued device command (WS command.updated). "sending" is internal and never published. */
export type CommandStatus = 'queued' | 'sending' | 'sent' | 'confirmed' | 'failed' | 'denied' | 'cancelled';
/** 202 answer of POST /zones/{id}/command and POST /lights/groups/{id}. */
export interface CommandAccepted {
  command_id: Uuid;
  status: 'queued';
}
/** WS command.updated payload. A retry keeps the same id (status "queued", retrying true). */
export interface CommandUpdate {
  id: Uuid;
  status: CommandStatus;
  detail?: string | null;
  attempts?: number;
  retrying?: boolean;
}

/* ------------------------------------------------------------------ climate */

export type HvacMode = 'off' | 'heat' | 'cool' | 'auto' | 'dry' | 'fan';
/** Fan speeds accepted by POST /zones/{id}/command. */
export type FanSpeed = 'auto' | 'low' | 'medium' | 'high';
/** Fan speeds accepted in button mapping steps. */
export type StepFanSpeed = FanSpeed | 'medium_low' | 'medium_high' | 'quiet' | 'turbo';
export type EnergyPriority = 'comfort' | 'balanced' | 'saving';
export type ZoneControl = 'schedule' | 'manual' | 'oem_fallback' | 'off';
export type UnitStatus = 'not_linked' | 'offline' | 'online';
export type NoticeLevel = 'info' | 'warn';

/** Zone.policy as returned by the API (all keys present). */
export interface ZonePolicyFull {
  band_c: number;
  min_interval_s: number;
  max_step_c: number;
  max_starts_per_hour: number;
  stale_after_s: number;
  plausible_min_c: number;
  plausible_max_c: number;
  max_offset_c: number;
  override_default_minutes: number;
  energy_priority: EnergyPriority;
  /** 0 = watchdog off, else 15..720. */
  watchdog_minutes: number;
  watchdog_min_progress_c: number;
}
/** PUT /zones/{id}/policy is partial: only the fields sent change. */
export type ZonePolicyIn = Partial<ZonePolicyFull>;
/** Body of PUT /zones/{id}/policy (partial). Kept for existing code; read Zone.policy as ZonePolicyFull. */
export type ZonePolicy = ZonePolicyIn;

export interface ZoneCapabilities {
  /** [] when no unit is linked. */
  modes: HvacMode[];
  fan: boolean;
  swing: boolean;
  min_c: number;
  max_c: number;
  step_c: number;
}
export interface Zone {
  id: Uuid;
  name: string;
  room: string | null;
  unit_device_id: Uuid | null;
  sensor_device_id: Uuid | null;
  /** false: no airco linked. Show neutral text, never red. */
  unit_linked: boolean;
  unit_status: UnitStatus;
  online: boolean;
  mode: HvacMode;
  target_c: number | null;
  fan: string | null;
  temperature: Measurement;
  humidity: Measurement;
  control: ZoneControl;
  /** Linked comfort profile's name, else the old label. */
  profile: string | null;
  profile_id: Uuid | null;
  override: { until: Ts | null; source: 'app' | 'remote_control' | 'user' | 'button' | (string & {}) | null } | null;
  /** "Terug naar schema" makes sense (manual or off, unit linked). A viewer still gets 403. */
  can_resume: boolean;
  /** false when the unit is offline or off: disable +/−, fan and "Handmatig 2 uur". */
  setpoint_adjustable: boolean;
  pending_command: { id: Uuid; status: CommandStatus; since: Ts | null } | null;
  capabilities: ZoneCapabilities;
  blocked_modes: { mode: HvacMode; reason: string }[];
  /** mode null = keeps the current mode. */
  next_change: { at: Ts; target_c: number; mode: HvacMode | null; label: string } | null;
  correction_c: number;
  energy_priority: EnergyPriority;
  last_decision: string | null;
  watchdog_since: Ts | null;
  policy: ZonePolicyFull;
  notice: string | null;
  notice_level: NoticeLevel | null;
}
export interface ZoneCommand {
  mode?: HvacMode;
  target_c?: number;
  fan?: FanSpeed;
  override_minutes?: number;
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
/** Without mode a block keeps the unit's current mode and never starts an off unit; "off" switches it off. */
export interface ScheduleBlock {
  days: number[]; // 0 = maandag … 6 = zondag
  start: string; // HH:MM
  target_c: number;
  mode?: HvacMode | null;
  label: string;
}
export interface Schedule {
  blocks: ScheduleBlock[];
  profile_id?: Uuid | null;
  profile?: string | null;
}
export interface ScheduleIn {
  blocks: ScheduleBlock[];
}
export interface ZoneConfig {
  name?: string;
  unit_device_id?: Uuid | null;
  sensor_device_id?: Uuid | null;
  outdoor_group?: string | null;
}
/** GET /homes/{id}/climate/roles */
export interface ClimateRole {
  role: string; // "outdoor_temp"
  label: string;
  device_id: Uuid | null;
  device_name: string | null;
}

/* ------------------------------------------------------------------ comfort profiles */

export interface Profile {
  id: Uuid;
  home_id: Uuid;
  name: string;
  target_c: number;
  band_c: number;
  mode: HvacMode | null;
  energy_priority: EnergyPriority;
  schedule: ScheduleBlock[];
  zone_ids: Uuid[];
  created_at: Ts | null;
}
export interface ProfileIn {
  name: string;
  target_c: number; // 10..30
  band_c?: number; // 0.2..3, default 0.5
  mode?: HvacMode | null;
  energy_priority?: EnergyPriority;
  schedule?: ScheduleBlock[];
}

/* ------------------------------------------------------------------ optimisation */

export interface AdaptiveModel {
  id: Uuid;
  version: number;
  params: { k_per_h: number; c_per_h: number; n: number; r2: number; usable: boolean; [k: string]: unknown };
  created_at: Ts | null;
  active: boolean;
}
export interface AdaptiveStatus {
  enabled: boolean;
  baseline: { samples: number; running_samples: number; required: number; since: Ts | null; ready: boolean };
  max_offset_c: number;
  models: AdaptiveModel[];
}
export type ExperimentStatus = 'defined' | 'running' | 'paused' | 'finished' | 'stopped';
export interface ExperimentVariant {
  label: string;
  start: Ts;
  end: Ts;
  zone_id?: Uuid;
  profile_id?: Uuid | null;
}
export interface Experiment {
  id: Uuid;
  name: string;
  zone_id: Uuid | null;
  variants: ExperimentVariant[];
  status: ExperimentStatus;
  auto_apply: boolean;
  /** Index into variants while running. */
  current_variant: number | null;
  paused_reason: string | null;
  created_at?: Ts;
}
/** What stopping or deleting an auto-apply experiment did to its zone: "restored" (profile and schedule
 * from before the experiment are back), "kept" (restore=false: the variant's profile stays), "changed"
 * (someone put another profile on the zone meanwhile; that stays) or "none" (no variant profile active). */
export type ZoneRestored = 'restored' | 'kept' | 'changed' | 'none';
/** POST /experiments/{id}/stop. */
export interface ExperimentStopped extends Experiment {
  zone_restored: ZoneRestored;
}
export interface ExperimentIn {
  name: string;
  zone_id: Uuid;
  /** 2..4; with auto_apply every variant needs a profile_id and none may overlap. */
  variants: { label: string; start: Ts; end: Ts; profile_id?: Uuid | null }[];
  auto_apply?: boolean;
}
export type Verdict = 'vergelijkbaar' | 'indicatief' | 'onvoldoende';
export interface ExperimentRow {
  variant: string;
  days: number;
  kwh_per_day: number | null;
  vs_first_pct: number | null;
  mean_abs_dev_c: number | null;
  starts: number;
  outdoor_mean_c: number | null;
  /** Share of time within ±band/2 of the variant's own targets (0..100). */
  comfort_pct: number | null;
  other_zone_mean_target_c: number | null;
  notes: string[];
}
export interface ExperimentResult extends Experiment {
  verdict: Verdict;
  reasons: string[];
  rows: ExperimentRow[];
  outdoor: { device_id: Uuid; name: string } | null;
  confounders: string[];
}

/* ------------------------------------------------------------------ lights & buttons */

/** Outcome of the newest command (last hour) for a group or lamp. Bridge errors count as "failed". */
export interface CommandOutcome {
  id: Uuid;
  action: string;
  status: CommandStatus;
  detail: string;
  at: Ts | null;
}
export interface LightGroup {
  id: Uuid;
  name: string;
  on: boolean;
  brightness: number | null;
  scene: string | null;
  scene_id: Uuid | null;
  reachable: boolean;
  lights_on: number;
  lights_total: number;
  lights_reachable: number | null;
  owner_type: 'room' | 'zone' | 'bridge_home' | (string & {}) | null;
  /** The "Alle lampen" group: sorted last; hide it or show it apart. */
  all_lights: boolean;
  hidden: boolean;
  last_command: CommandOutcome | null;
}
export interface Scene {
  id: Uuid;
  name: string;
  group_id: Uuid | null;
  active: boolean;
}
export interface Lamp {
  id: Uuid;
  name: string;
  on: boolean;
  brightness: number | null;
  reachable: boolean;
  model: string | null;
  last_command: CommandOutcome | null;
}
export interface Lights {
  groups: LightGroup[];
  scenes: Scene[];
  lights: Lamp[];
}
export interface GroupCommand {
  on?: boolean;
  brightness?: number; // 1..100
  scene_id?: Uuid;
}

export type ButtonEventName =
  | 'initial_press'
  | 'repeat'
  | 'short_release'
  | 'long_press'
  | 'long_release'
  | 'rotate_clock_wise'
  | 'rotate_counter_clock_wise'
  | (string & {});
/** One physical button (or rotary) of a Hue switch; id is the 36-char Hue resource id. */
export interface ButtonInput {
  id: string;
  control_id: number | null;
  type: 'button' | 'relative_rotary' | (string & {});
  events: ButtonEventName[];
  /** The Hue Bridge itself also acts on this button. */
  bridge_rule: boolean;
}
export type MappingMode = 'lights' | 'climate' | 'combined';
export type LightStep = {
  type: 'light';
  device_id: Uuid; // group, lamp or scene device
  action: 'set_group' | 'set_light' | 'recall_scene' | 'toggle';
  params?: { on?: boolean; brightness?: number };
};
export type ZoneStep =
  | { type: 'zone'; zone_id: Uuid; mode?: HvacMode; target_c?: number; fan?: StepFanSpeed; override_minutes?: number }
  | { type: 'zone'; zone_id: Uuid; action: 'resume' };
export type MappingStep = LightStep | ZoneStep;
/** "lights" allows only light steps, "climate" only zone steps, "combined" both (and combined_confirmed). */
export type MappingAction = { steps: MappingStep[] };
export interface ButtonMapping {
  id: Uuid;
  button: string;
  event: ButtonEventName;
  mode: MappingMode;
  action: MappingAction;
  combined_confirmed: boolean;
  enabled: boolean;
  conflict: string | null;
}
export interface ButtonMappingIn {
  button: string;
  event: ButtonEventName;
  mode: MappingMode;
  action: MappingAction;
  combined_confirmed?: boolean;
  enabled?: boolean;
}
export type ButtonMappingPatch = Partial<ButtonMappingIn>;
export interface HueButton {
  id: Uuid;
  name: string;
  model: string | null;
  online: boolean;
  last_seen: Ts | null;
  battery: number | null;
  buttons: ButtonInput[];
  events: ButtonEventName[];
  bridge_rules: string[];
  bridge_rule_names: string[];
  last_event: { button: string; event: ButtonEventName; at: Ts } | null;
  mapping: ButtonMapping[];
}
export interface SimulatedStep {
  type: 'light' | 'zone';
  result: 'would_run' | 'refused' | 'invalid';
  reason: string | null;
  code?: string;
  action?: string;
  params?: Record<string, unknown>;
  device_id?: Uuid;
  device_name?: string;
  zone_id?: Uuid;
  zone_name?: string;
}
export interface SimulateResult {
  button: string;
  event: string;
  would_run: boolean;
  bridge_rule: boolean;
  message: string;
  mappings: { mapping_id: Uuid; mode: MappingMode; conflict: string | null; skipped?: string; steps: SimulatedStep[] }[];
}

/* ------------------------------------------------------------------ energy */

export type Period = 'day' | 'week' | 'month' | 'year';
/** Role keys: grid, grid_export, airco, boiler, rest (and gas in exports). */
export type MeterRole = 'grid' | 'grid_export' | 'airco' | 'boiler' | 'rest' | 'gas' | (string & {});
export interface EnergyMeter {
  role: MeterRole;
  label: string;
  kwh: number | null;
  unit: 'kWh';
  cost: number | null;
  /** Share of this meter's kWh that has a tariff (0..1). */
  cost_coverage: number;
  /** "rest" is "derived", or "estimated" when a sub-meter has gaps or the parts exceed the P1. */
  quality: Quality;
  note: string | null;
  notes: string[];
}
export interface EnergyBucket {
  start: Ts;
  end: Ts;
  values: Record<string, number>;
  complete: boolean;
  /** Grid variable cost minus export for this bucket; null if unpriced. */
  cost: number | null;
  notes: string[];
  /** Only when the home has gas data. */
  gas_m3?: number;
}
export interface GasSummary {
  m3: number;
  unit: 'm³';
  cost: number | null;
  cost_coverage: number;
  quality: Quality;
  note: string | null;
}
export interface EnergySummary {
  period: Period;
  start: Ts;
  end: Ts;
  timezone: string;
  total_kwh: number | null;
  /** Sum of the priced parts: import − export + fixed + gas. */
  cost: number | null;
  /** Share of grid kWh that has a tariff (0..1). */
  cost_coverage: number;
  fixed_cost: number | null;
  fixed_days: number;
  /** Dutch note when only part is priced or no tariff applies. */
  cost_note: string | null;
  currency: string;
  meters: EnergyMeter[];
  gas: GasSummary | null;
  buckets: EnergyBucket[];
}
export interface LivePart {
  role: string;
  label: string;
  /** null with quality "missing" when the sub-meter is stale. */
  watts: number | null;
  quality: Quality;
  at: Ts | null;
  stale: boolean;
  note?: string | null;
}
export interface LivePower {
  /** Time of the P1 reading. */
  at: Ts;
  total_w: number | null;
  complete: boolean;
  note: string | null;
  parts: LivePart[];
}
export interface Tariff {
  id: Uuid;
  valid_from: string; // YYYY-MM-DD
  import_per_kwh: number;
  export_per_kwh: number;
  fixed_per_day: number;
  import_t1_per_kwh: number | null;
  import_t2_per_kwh: number | null;
  gas_per_m3: number | null;
  source: string;
}
/** POST /homes/{id}/tariffs and PUT /tariffs/{id}. Optional fields accept "" or null and decimal commas;
 * give both T1 and T2 or neither. 409 tariff_exists when that start date already exists. */
export interface TariffIn {
  valid_from: string;
  import_per_kwh: number | string;
  export_per_kwh?: number | string | null;
  fixed_per_day?: number | string | null;
  import_t1_per_kwh?: number | string | null;
  import_t2_per_kwh?: number | string | null;
  gas_per_m3?: number | string | null;
}
/** @deprecated use TariffIn */
export type NewTariff = TariffIn;
/** GET/PUT /energy/limits: maximum plausible power in kW (gas: m³/h). */
export interface EnergyLimits {
  grid: number;
  airco: number;
  boiler: number;
  gas: number;
}
/** null resets that limit to the default. */
export type EnergyLimitsIn = Partial<Record<keyof EnergyLimits, number | null>>;
export type ExportFormat = 'csv' | 'json';
export type ExportResolution = 'hour' | 'day' | 'raw';
export interface ExportOptions {
  format: ExportFormat;
  from?: string; // YYYY-MM-DD
  to?: string;
  /** raw: at most 31 days (422 range_too_large). */
  resolution?: ExportResolution;
}

export interface Overview {
  home: Home;
  status: StatusCheck[];
  zones: Zone[];
  lights: LightGroup[];
  energy_today: EnergySummary;
  power_now: LivePower | null;
  alerts: Alert[];
}

/* ------------------------------------------------------------------ devices */

export type DeviceKind =
  | 'p1_meter'
  | 'kwh_meter'
  | 'energy_socket'
  | 'meter'
  | 'ac_unit'
  | 'zigbee_sensor'
  | 'zigbee_device'
  | 'zigbee_bridge'
  | 'hue_bridge'
  | 'hue_device'
  | 'light'
  | 'light_group'
  | 'scene'
  | 'button'
  | 'device'
  | 'unknown'
  | (string & {});
export type Lifecycle = 'active' | 'archived' | 'replaced' | (string & {});
export type Compatibility = 'tested' | 'untested' | 'unsupported';
export interface RelayDetails {
  on: boolean | null;
  switch_lock: boolean | null;
  state: 'ok' | 'unsupported' | null;
  led_brightness: number | null;
}
export interface UnitDetails {
  mode: string | null;
  target_c: number | null;
  indoor_c: number | null;
  indoor_sensor: boolean | null;
  fan: string | null;
  louver: string | null;
  display_unit: string | null;
}
export interface ZigbeeDetails {
  friendly_name?: string;
  interview_completed?: boolean;
  supported?: boolean;
  power_source?: string;
  availability?: string;
  bridge_state?: string;
  mqtt_connected?: boolean;
  zigbee2mqtt_version?: string;
}
export interface DeviceDetails {
  relay?: RelayDetails;
  caps?: ZoneCapabilities | null;
  unit?: UnitDetails;
  zigbee?: ZigbeeDetails;
  /** Buttons: counts. */
  buttons?: number;
  bridge_rules?: number;
}
export interface Calibration {
  temperature_offset: number;
  humidity_offset: number;
}
export interface Device {
  id: Uuid;
  provider: string;
  kind: DeviceKind;
  name: string;
  stable_identity: string;
  model: string | null;
  firmware: string | null;
  room: string | null;
  room_id: Uuid | null;
  integration_id: Uuid | null;
  roles: string[];
  /** Roles that fit this device kind (use for the role picker). */
  allowed_roles: string[];
  online: boolean;
  last_seen: Ts | null;
  capabilities: string[];
  protected: boolean;
  compatibility: Compatibility;
  tested_at: Ts | null;
  lifecycle: Lifecycle;
  calibration: Calibration;
  details: DeviceDetails;
}
/** POST /integrations/{provider}/pair success. */
export type PairResult = Device & { integration_id: Uuid; repaired: boolean };
export interface DevicePatch {
  name?: string;
  room_id?: Uuid | null;
  /** "active" brings an archived device back. */
  lifecycle?: 'active';
}
export interface CalibrationIn {
  temperature_offset?: number | null; // -10..10
  humidity_offset?: number | null; // -30..30
}
export interface ReadingValue {
  value: number | null;
  unit: string | null;
  quality: Quality | string;
  at: Ts;
  raw: unknown;
}
export interface DeviceTestCheck {
  key: 'seen' | 'connection' | 'mqtt' | 'dongle' | 'readings' | 'relay' | (string & {});
  label: string;
  state: CheckState;
  detail: string;
  /** e.g. key_mismatch, identity_mismatch */
  code?: string | null;
}
/** POST /devices/{id}/test: reads only, never switches anything. */
export interface DeviceTest {
  device_id: Uuid;
  ok: boolean;
  checked_at: Ts;
  checks: DeviceTestCheck[];
  readings: Record<string, ReadingValue>;
  note: string;
}
export interface DeviceDiagnostics {
  diagnostics: Record<string, unknown>;
  /** Pretty JSON, ready to copy. */
  text: string;
}
export interface ReplaceResult {
  old: Device;
  new: Device;
  moved: { roles: string[]; zones: number; mappings: number };
}

/* ------------------------------------------------------------------ integrations */

export type IntegrationState = 'ok' | 'warn' | 'error' | 'unknown' | 'not_configured';
/** GET /integrations. Rows with id null are providers without an enabled integration. */
export interface Integration {
  id: Uuid | null;
  provider: string;
  label: string;
  provider_label?: string;
  home_id?: Uuid | null;
  enabled?: boolean;
  identity?: string | null;
  host?: string | null;
  state: IntegrationState | (string & {});
  detail: string | null;
  last_ok_at: Ts | null;
  code?: string | null;
  connected_since?: Ts | null;
  missed_polls?: number;
  checked_at?: Ts | null;
  has_diagnostics?: boolean;
}
/** GET /integrations/{id} (admin). */
export interface IntegrationDetail extends Integration {
  id: Uuid;
  health: { health_state: string | null; health_detail: string | null; checked_at: Ts | null; extra: Record<string, unknown> };
  config: Record<string, unknown>;
  diagnostics: Record<string, unknown>;
  devices: { id: Uuid; name: string; kind: DeviceKind; online: boolean; lifecycle: Lifecycle }[];
}
/** PATCH /integrations/{id}: address and/or name. verify (default true) probes the new host first. */
export interface IntegrationPatch {
  host?: string;
  name?: string;
  verify?: boolean;
}
export interface CompatibilityProvider {
  provider: string;
  label: string;
  works_locally: string;
  needs_manufacturer_app: string | null;
}
export interface CompatibilityRow {
  provider: string;
  model: string;
  protocol: string;
  local: string;
  vendor_app: string | null;
  status: Compatibility;
  devices: { id: Uuid; name: string; model: string | null; compatibility: Compatibility; tested_at: Ts | null }[];
  counts: { tested: number; untested: number; unsupported: number };
}
/** GET /integrations/compatibility */
export interface CompatibilityMatrix {
  providers: CompatibilityProvider[];
  rows: CompatibilityRow[];
  note: string;
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
/** 202 from POST /integrations/{provider}/pair: a human step is needed (link button, button press). */
export interface ActionRequired extends PairingInstructions {
  status: 'action_required';
}
export interface PairIn {
  home_id: Uuid;
  host: string;
  name?: string;
  /** Gree: { encryption: "auto" | "v1" | "v2" }. */
  options?: Record<string, unknown>;
}
/** GET /integrations/{provider}/instructions */
export interface IntegrationInstructions {
  provider: string;
  label: string;
  steps: string[];
  /** What must happen in the manufacturer's app first (null = nothing). */
  needs_manufacturer_app: string | null;
  /** What works locally, in Dutch. */
  works_locally: string;
}
/** Energy/climate roles a device can be bound to. Prefer Device.allowed_roles for the picker. */
export const BIND_ROLES = [
  { key: 'grid', label: 'Huis totaal (P1)' },
  { key: 'airco', label: 'Airco totaal (kWh-meter)' },
  { key: 'boiler', label: 'Boiler (alleen meten)' },
  { key: 'outdoor_temp', label: 'Buitentemperatuur' },
] as const;
export interface PermitJoinResult {
  command_id: Uuid;
  open_until: Ts;
}

/* ------------------------------------------------------------------ system */

export interface SystemSettings {
  lan_url: string | null;
  remote_url: string | null;
  safe_mode: boolean;
  maintenance: boolean;
}
export interface AddressesIn {
  lan_url: string | null;
  remote_url: string | null;
}
export type BackupStatus = 'running' | 'ok' | 'failed';
/** "export": full export (.shcbak, restorable in the app, with Zigbee2MQTT data). "db_dump": encrypted
 * pg_dump of the database only (.shcdump), restored on the command line (shc-api decrypt-dump + pg_restore). */
export type BackupKind = 'export' | 'db_dump';
export interface Backup {
  id: Uuid;
  created_at: Ts;
  kind: BackupKind | (string & {});
  /** true = made by the automatic schedule (only these are pruned). */
  auto: boolean;
  filename: string | null;
  size_bytes: number | null;
  sha256: string | null;
  checksum: string | null;
  status: BackupStatus | (string & {});
  app_version: string | null;
  schema_revision: string | null;
  secrets_included: boolean | null;
  zigbee2mqtt_included: boolean | null;
  error: string | null;
  detail: { tables?: Record<string, number>; secrets_included?: boolean; zigbee2mqtt_included?: boolean; [k: string]: unknown } | null;
  downloadable: boolean;
}
export interface BackupIn {
  passphrase: string; // ≥ 12
  include_secrets: boolean;
}
/** "daily_full": every day a full export (default). "dump_daily_full_weekly": every day an encrypted
 * database dump, and once a week (at least every 7 days) the full export. */
export type BackupSchedule = 'daily_full' | 'dump_daily_full_weekly';
/** GET/PUT /system/backup-settings (admin). The passphrase is never returned. */
export interface BackupSettings {
  enabled: boolean;
  wanted: boolean;
  passphrase_set: boolean;
  passphrase_source: 'app' | 'file' | null;
  passphrase_error: string | null;
  hour: number;
  keep: number;
  timezone: string;
  schedule: BackupSchedule;
  /** What the next automatic backup will be: a full export or a database dump. */
  next_kind: 'full' | 'dump';
  /** Last automatic full export. */
  last_auto_backup: Ts | null;
  /** Last automatic database dump (only with schedule dump_daily_full_weekly). */
  last_auto_dump: Ts | null;
  next_due_at: Ts | null;
  due_now: boolean;
  last_error: string | null;
  failures: number;
  retry_after: Ts | null;
  last_backup: Backup | null;
  zigbee2mqtt_in_last_backup: boolean | null;
}
export interface BackupSettingsIn {
  passphrase?: string;
  clear_passphrase?: boolean;
  hour?: number; // 0..23, home time zone
  keep?: number; // 1..365, per kind (exports and dumps are pruned separately)
  enabled?: boolean;
  schedule?: BackupSchedule;
}
/** GET/PUT /system/notify (web admin): push message outside the app (e.g. ntfy) when the worker or the
 * backups stop. The address itself is a secret and never returned, only scheme and host. */
export interface NotifySettings {
  configured: boolean;
  /** "app" = set on this page (stored encrypted), "env" = SHC_NOTIFY_URL on the server, null = not set. */
  source: 'app' | 'env' | null;
  /** e.g. "https://ntfy.sh/…". */
  url_hint: string | null;
  /** Dutch explanation when the address stored in the app cannot be decrypted with this server's
   * master key (restore on a new NAS, key change): it is not used and must be set again; else null. */
  error?: string | null;
  /** What triggers a message (Dutch). */
  events?: string[];
}
/** null (or empty) removes the address set in the app; then SHC_NOTIFY_URL applies, if set. */
export interface NotifyIn {
  url: string | null;
}
/** POST /system/notify/test. */
export interface NotifyTestResult {
  ok: boolean;
  error: string | null;
}
export type RetentionField = 'power_days' | 'counter_days' | 'log_days';
/** GET/PUT /system/retention (admin): days raw data is kept. Hourly energy/gas totals are kept forever. */
export interface RetentionSettings {
  power_days: number;
  counter_days: number;
  log_days: number;
  /** "app" = set here, "env" = SHC_RETENTION_*_DAYS default. */
  source: Record<RetentionField, 'app' | 'env'>;
  defaults: Record<RetentionField, number>;
  minimums: Record<RetentionField, number>;
  maximum: number;
  note: string;
}
/** Omitted fields stay; null goes back to the default. */
export type RetentionIn = Partial<Record<RetentionField, number | null>>;
export type SchemaRelation = 'same' | 'older' | 'newer' | 'unknown';
/** POST /system/restore/verify: nothing is changed yet. */
export interface RestoreVerify {
  upload_id: string;
  filename: string | null;
  size_bytes: number;
  format_version: number;
  created_at: Ts | null;
  app_version: string | null;
  schema_revision: string | null;
  current: { app_version: string; schema_revision: string | null };
  schema_relation: SchemaRelation;
  restorable: boolean;
  refusal: string | null;
  secrets_included: boolean;
  redacted: boolean;
  zigbee2mqtt_included: boolean;
  zigbee2mqtt: unknown;
  tables: Record<string, number>;
  total_records: number;
  homes: { id: Uuid; name: string; timezone: string; active: boolean }[];
  devices: { id: Uuid; home_id: Uuid; provider: string; kind: DeviceKind; name: string; stable_identity: string; model: string | null; capabilities: string[] }[];
  integrations: { id: Uuid; home_id: Uuid; provider: string; name: string; has_credentials: boolean }[];
  credentials: { ok: number; failed: string[]; total: number };
  warnings: string[];
}
/** A: same home on a new server. B: new home (old one archived, its integrations off). */
export type RestoreMode = 'same_home' | 'new_home';
export interface RestoreApplyIn {
  upload_id: string;
  passphrase: string;
  mode: RestoreMode;
}
export interface RestoreApplyResult {
  job_id: string;
  status: 'running';
  poll: string;
}
export interface RestoreReport {
  schema_from: string | null;
  schema_to: string | null;
  migrated: boolean;
  counts: Record<string, number>;
  total_records: number;
  /** What could not be taken over: records per table that no longer exists, column names per table, and
   * records per table that the database update (migration) removed. */
  dropped: { tables: Record<string, number>; columns: Record<string, string[]>; rows_removed_by_migration: Record<string, number> };
  mode: RestoreMode;
  new_home_id: Uuid | null;
  archived_homes: Uuid[];
  zigbee2mqtt_included: boolean;
}
/** GET /system/restore/jobs/{job_id} (no login: the job id is a long secret). */
export interface RestoreJob {
  job_id: string;
  state: 'running' | 'done' | 'failed';
  step: string;
  mode: RestoreMode;
  started_at: Ts;
  finished_at: Ts | null;
  error: string | null;
  report: RestoreReport | null;
}
export type CompareStatus = 'compatible' | 'partial' | 'unavailable';
export interface CompareDevice {
  id: Uuid;
  home_id: Uuid;
  name: string;
  provider: string;
  kind: DeviceKind;
  stable_identity: string;
  status: CompareStatus | 'new';
  reason: string;
  online: boolean;
  last_seen: Ts | null;
  missing: string[];
}
export interface CompareFunction {
  /** "role:<key>", "zone:<id>:airco|sensor", "lights:<id>" */
  key: string;
  label: string;
  home_id: Uuid;
  device_id: Uuid | null;
  device_name: string | null;
  status: CompareStatus;
  reason: string;
  suggestion: string | null;
}
/** GET /system/restore/compare (admin). */
export interface RestoreCompare {
  restored_at: Ts | null;
  safe_mode: boolean;
  restored_from: { created_at: Ts | null; app_version: string | null; schema_revision: string | null; restored_at: Ts; mode: RestoreMode } | null;
  devices: CompareDevice[];
  functions: CompareFunction[];
  summary: Record<CompareStatus, number>;
}

/* ------------------------------------------------------------------ websocket */

export interface HelloData {
  user: User;
  via: 'cookie' | 'token';
  client_role: ClientRole | null;
  can_control: boolean;
}
/** Sent right before the server closes with 4401 (code auth_required) or 4403 (token_revoked/expired). */
export interface WsErrorData {
  code: 'auth_required' | 'token_revoked' | 'token_expired' | (string & {});
  /** e.g. logout, password_changed, password_reset, user_disabled, user_deleted, expired, origin_mismatch, no_credentials */
  reason?: string;
}

export type WsMessage =
  | { type: 'hello'; data: HelloData }
  | { type: 'error'; data: WsErrorData }
  /** The real event was too large for NOTIFY: refetch. data.reason = the original event type. */
  | { type: 'refresh'; data: { reason?: string } }
  | { type: 'zone.updated'; data: Zone }
  | { type: 'lights.updated'; data: LightGroup }
  | { type: 'energy.live'; data: LivePower }
  | { type: 'alert.created'; data: Alert }
  | { type: 'alert.resolved'; data: { id: Uuid } }
  /** Replaces Overview.status. */
  | { type: 'status.updated'; data: StatusCheck[] }
  | { type: 'command.queued'; data: { id: Uuid } }
  | { type: 'command.updated'; data: CommandUpdate }
  | { type: 'integration.changed'; data: { id: Uuid } };
