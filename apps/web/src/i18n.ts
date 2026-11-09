// Dutch labels for raw API values. Never show raw keys ("initial_press", "homewizard", "Override")
// in the UI: use t(DICT, value). Unknown values get a readable fallback instead of the raw key.
export { CONTROL_LABEL, MODE_LABEL, QUALITY_LABEL } from './format';

export type Dict = Readonly<Record<string, string>>;

/** Label for `key`, else a readable fallback ("long_press_x" -> "long press x"), else `fallback`. */
export function t(dict: Dict, key: string | null | undefined, fallback = '–'): string {
  if (key === null || key === undefined || key === '') return fallback;
  return dict[key] ?? key.replace(/[_:.]+/g, ' ').trim();
}

/** Hue switch events (ButtonEvent.event). */
export const BUTTON_EVENT_LABEL: Dict = {
  initial_press: 'Indrukken',
  short_release: 'Kort indrukken',
  long_press: 'Lang indrukken',
  long_release: 'Loslaten na lang indrukken',
  repeat: 'Ingedrukt houden',
  rotate_clock_wise: 'Draaien met de klok mee',
  rotate_counter_clock_wise: 'Draaien tegen de klok in',
};
export const BUTTON_TYPE_LABEL: Dict = { button: 'Knop', relative_rotary: 'Draaiknop' };
export const MAPPING_MODE_LABEL: Dict = { lights: 'Licht', climate: 'Klimaat', combined: 'Licht en klimaat' };
export const LIGHT_ACTION_LABEL: Dict = {
  set_group: 'Groep instellen',
  set_light: 'Lamp instellen',
  recall_scene: 'Scène kiezen',
  toggle: 'Aan/uit wisselen',
  resume: 'Terug naar schema',
  set_hvac: 'Airco instellen',
  permit_join: 'Zigbee-koppelvenster',
};
export const STEP_RESULT_LABEL: Dict = { would_run: 'Wordt uitgevoerd', refused: 'Geweigerd', invalid: 'Ongeldig' };

export const PROVIDER_LABEL: Dict = {
  hue: 'Philips Hue',
  homewizard: 'HomeWizard',
  gree: 'Gree airco',
  zigbee: 'Zigbee',
};

export const KIND_LABEL: Dict = {
  p1_meter: 'P1-meter',
  kwh_meter: 'kWh-meter',
  energy_socket: 'Energy Socket (stekker)',
  meter: 'Meter',
  ac_unit: 'Airco-unit',
  zigbee_sensor: 'Zigbee-sensor',
  zigbee_device: 'Zigbee-apparaat',
  zigbee_bridge: 'Zigbee-coördinator',
  hue_bridge: 'Hue Bridge',
  hue_device: 'Hue-apparaat',
  light: 'Lamp',
  light_group: 'Lichtgroep',
  scene: 'Scène',
  button: 'Knop',
  device: 'Apparaat',
  unknown: 'Onbekend apparaat',
};

/** Logical roles (POST /devices/{id}/bind). */
export const ROLE_LABEL: Dict = {
  grid: 'Huis totaal (P1)',
  grid_export: 'Teruglevering',
  airco: 'Airco totaal (kWh-meter)',
  boiler: 'Boiler (alleen meten)',
  outdoor_temp: 'Buitentemperatuur',
  living_room_climate: 'Klimaat woonkamer',
  bedroom_climate: 'Klimaat slaapkamer',
  rest: 'Overig huis',
  gas: 'Gas',
};

export const LIFECYCLE_LABEL: Dict = { active: 'In gebruik', archived: 'Gearchiveerd', replaced: 'Vervangen' };
export const COMPATIBILITY_LABEL: Dict = { tested: 'getest', untested: 'nog niet getest', unsupported: 'niet ondersteund' };

export const CHECK_STATE_LABEL: Dict = { ok: 'In orde', warn: 'Let op', error: 'Probleem', unknown: 'Onbekend', not_configured: 'Niet ingesteld' };
export const SEVERITY_LABEL: Dict = { info: 'Info', warn: 'Waarschuwing', error: 'Storing' };
export const UNIT_STATUS_LABEL: Dict = { not_linked: 'Geen airco gekoppeld', offline: 'Niet bereikbaar', online: 'Bereikbaar' };

export const COMMAND_STATUS_LABEL: Dict = {
  queued: 'In de wachtrij',
  sending: 'Wordt verzonden',
  sent: 'Verzonden, nog niet bevestigd',
  confirmed: 'Bevestigd',
  failed: 'Mislukt',
  denied: 'Geweigerd',
  cancelled: 'Vervallen',
};

export const FAN_LABEL: Dict = {
  auto: 'Automatisch',
  low: 'Laag',
  medium_low: 'Middel-laag',
  medium: 'Middel',
  medium_high: 'Middel-hoog',
  high: 'Hoog',
  quiet: 'Stil',
  turbo: 'Turbo',
};
export const ENERGY_PRIORITY_LABEL: Dict = { comfort: 'Comfort', balanced: 'Gebalanceerd', saving: 'Zuinig' };
export const OVERRIDE_SOURCE_LABEL: Dict = {
  app: 'via de app',
  user: 'via de app',
  remote_control: 'met de afstandsbediening',
  button: 'met een knop',
};
export const DAY_LABEL = ['maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag', 'zondag'] as const;
export const DAY_SHORT = ['ma', 'di', 'wo', 'do', 'vr', 'za', 'zo'] as const;

export const EXPERIMENT_STATUS_LABEL: Dict = { defined: 'Gepland', running: 'Loopt', paused: 'Gepauzeerd', finished: 'Afgerond', stopped: 'Gestopt' };

export const USER_ROLE_LABEL: Dict = { admin: 'Beheerder', viewer: 'Kijker' };
/** Role of a paired phone. */
export const CLIENT_ROLE_LABEL: Dict = { admin: 'Beheren', control: 'Bedienen', viewer: 'Alleen kijken' };
export const CLIENT_ROLE_HELP: Dict = {
  control: 'Klimaat en licht bedienen; geen instellingen, gebruikers of back-ups.',
  viewer: 'Alleen kijken; niets bedienen.',
  admin: 'Alles, ook gebruikers, telefoons en back-ups. Vraagt je wachtwoord.',
};

export const BACKUP_STATUS_LABEL: Dict = { running: 'Bezig', ok: 'Gelukt', failed: 'Mislukt' };
export const SCHEMA_RELATION_LABEL: Dict = {
  same: 'Zelfde versie als deze server',
  older: 'Oudere versie: wordt na het terugzetten bijgewerkt',
  newer: 'Nieuwere versie: kan hier niet worden teruggezet',
  unknown: 'Versie onbekend',
};
export const RESTORE_MODE_LABEL: Dict = {
  same_home: 'A · Zelfde woning, nieuwe server',
  new_home: 'B · Nieuwe woning (verhuisd)',
};
export const COMPARE_STATUS_LABEL: Dict = { compatible: 'Werkt', partial: 'Gedeeltelijk', unavailable: 'Niet beschikbaar', new: 'Nieuw gevonden' };
export const RESTORE_STATE_LABEL: Dict = { running: 'Bezig', done: 'Klaar', failed: 'Mislukt' };

/** Audit log actions (GET /audit). "request" rows are generic API calls. */
export const AUDIT_ACTION_LABEL: Dict = {
  request: 'Wijziging via de app',
  setup_admin: 'Beheerder aangemaakt (eerste start)',
  login: 'Ingelogd',
  login_failed: 'Inloggen mislukt',
  user_locked: 'Account tijdelijk geblokkeerd',
  user_created: 'Gebruiker aangemaakt',
  user_updated: 'Gebruiker gewijzigd',
  user_deleted: 'Gebruiker verwijderd',
  password_changed: 'Wachtwoord gewijzigd',
  password_reset: 'Wachtwoord opnieuw ingesteld',
  pairing_code_created: 'Koppelcode gemaakt',
  phone_paired: 'Telefoon gekoppeld',
  phone_revoked: 'Telefoon ingetrokken',
  phone_unpaired: 'Telefoon ontkoppeld',
  phone_token_refreshed: 'Telefoonsleutel vernieuwd',
  home_created: 'Woning aangemaakt',
  home_archived: 'Woning gearchiveerd',
  settings_updated: 'Adressen gewijzigd',
  safe_mode_released: 'Veilige modus opgeheven',
  backup_started: 'Back-up gestart',
  backup_created: 'Back-up gemaakt',
  backup_settings_updated: 'Back-upinstellingen gewijzigd',
  restore_verified: 'Back-up gecontroleerd',
  restore_started: 'Herstel gestart',
  integration_paired: 'Apparaat gekoppeld',
  integration_address: 'Adres van koppeling gewijzigd',
  integration_removed: 'Koppeling verwijderd',
  role_bound: 'Rol gekoppeld',
  role_unbound: 'Rol losgekoppeld',
  device_archived: 'Apparaat gearchiveerd',
  device_restored: 'Apparaat teruggezet',
  device_replaced: 'Apparaat vervangen',
  device_calibrated: 'Apparaat gekalibreerd',
  device_tested_probe: 'Apparaat getest (uitlezen)',
  device_marked_tested: 'Apparaat als getest gemarkeerd',
  schedule_updated: 'Schema aangepast',
  zone_policy_updated: 'Regelgrenzen aangepast',
  zone_updated: 'Zone-koppeling aangepast',
  profile_created: 'Profiel aangemaakt',
  profile_updated: 'Profiel gewijzigd',
  profile_deleted: 'Profiel verwijderd',
  experiment_created: 'Experiment aangemaakt',
  experiment_paused: 'Experiment gepauzeerd',
  experiment_resumed: 'Experiment hervat',
  experiment_stopped: 'Experiment gestopt',
  experiment_deleted: 'Experiment verwijderd',
  adaptive_fit: 'Adaptief model berekend',
  adaptive_activate: 'Adaptief model aangezet',
  tariff_added: 'Tarief toegevoegd',
  tariff_updated: 'Tarief gewijzigd',
  tariff_deleted: 'Tarief verwijderd',
  energy_limits_updated: 'Meetgrenzen gewijzigd',
  button_mappings: 'Knopkoppelingen opgeslagen',
  button_mapping_added: 'Knopkoppeling toegevoegd',
  button_mapping_updated: 'Knopkoppeling gewijzigd',
  button_mapping_removed: 'Knopkoppeling verwijderd',
  zone_command: 'Klimaatcommando',
  zone_resume: 'Terug naar schema',
};

/** Why a web session or phone was signed out (WS error reason / auth notice). */
export const SIGNOUT_REASON_LABEL: Dict = {
  logout: 'Je bent uitgelogd.',
  password_changed: 'Je wachtwoord is gewijzigd. Log in met je nieuwe wachtwoord.',
  password_reset: 'Een beheerder heeft je wachtwoord opnieuw ingesteld.',
  user_disabled: 'Je account is uitgeschakeld. Vraag een beheerder om hulp.',
  user_deleted: 'Je account bestaat niet meer.',
  expired: 'Je sessie is verlopen. Log opnieuw in.',
  phone_revoked: 'De toegang van dit apparaat is ingetrokken.',
  unpaired: 'Dit apparaat is ontkoppeld.',
  no_credentials: 'Je sessie is verlopen. Log opnieuw in.',
  origin_mismatch: 'De live-verbinding werd geweigerd omdat het adres niet overeenkomt. Log opnieuw in.',
};

/** Keys in integration health details (StatusCheck.details / .extra). */
export const HEALTH_FIELD_LABEL: Dict = {
  code: 'Code',
  since: 'Sinds',
  health_state: 'Gezondheid',
  health_detail: 'Toelichting',
  checked_at: 'Laatst gecontroleerd',
  connected_since: 'Verbonden sinds',
  missed_polls: 'Gemiste uitleesrondes',
  consecutive_failures: 'Mislukte pogingen op rij',
  mqtt_connected: 'MQTT verbonden',
  mqtt_connected_since: 'MQTT verbonden sinds',
  mqtt_disconnected_since: 'MQTT weg sinds',
  mqtt_error: 'MQTT-fout',
  bridge_state: 'Zigbee2MQTT',
  bridge_online: 'Zigbee2MQTT online',
  bridge_seen_at: 'Zigbee2MQTT laatst gezien',
  zigbee2mqtt_version: 'Zigbee2MQTT-versie',
  coordinator_type: 'Coördinator',
  devices: 'Apparaten',
  stale_devices: 'Verouderde sensoren',
  eventstream_connected: 'Gebeurtenissen verbonden',
  disconnected_since: 'Verbroken sinds',
  last_event_at: 'Laatste gebeurtenis',
  last_reload_at: 'Laatst herladen',
  eventstream_error: 'Fout gebeurtenissen',
  connections: 'Verbindingen',
  v1_rules_available: 'Bridge-regels leesbaar',
  breaker: 'Beveiliging',
  bridge_id: 'Bridge-ID',
  host: 'Adres',
  mac: 'MAC-adres',
  encryption: 'Versleuteling',
  last_ok_at: 'Laatst gelukt',
  last_error: 'Laatste fout',
  last_error_at: 'Laatste fout om',
  compatibility: 'Compatibiliteit',
  api_version: 'API-versie',
  serial: 'Serienummer',
  found_serial: 'Gevonden serienummer',
  product_type: 'Producttype',
  identity_verified: 'Identiteit gecontroleerd',
  identity_checked_at: 'Identiteit gecontroleerd om',
};

/** Fallback texts for error codes without a server message (normally the server's Dutch message is shown). */
export const ERROR_CODE_LABEL: Dict = {
  api_unavailable: 'De app start op of wordt bijgewerkt. Probeer het over een paar seconden opnieuw.',
  rate_limited: 'Te veel pogingen achter elkaar. Wacht even en probeer het opnieuw.',
  request_too_large: 'Het verzoek is te groot.',
  payload_too_large: 'Het bestand is te groot.',
  mode_required: 'Kies eerst een modus.',
  lan_url_required: 'Stel eerst het thuisadres in via Systeem › Adressen.',
};
