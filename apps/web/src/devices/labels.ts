// Dutch texts for the devices page that part A's dictionary (i18n.ts) does not have.
import type { Device, DeviceKind } from '../api/types';
import { KIND_LABEL, ROLE_LABEL, t } from '../i18n';

export const METRIC_LABEL: Record<string, string> = {
  temperature: 'Temperatuur',
  humidity: 'Luchtvochtigheid',
  battery: 'Batterij',
  linkquality: 'Signaalsterkte',
  pressure: 'Luchtdruk',
  power_w: 'Vermogen',
  energy_import_kwh: 'Meterstand verbruik',
  energy_export_kwh: 'Meterstand teruglevering',
  gas_m3: 'Meterstand gas',
  target_c: 'Doeltemperatuur',
};

/** What to do for an integration/test error code. */
export const CODE_HINT: Record<string, string> = {
  device_offline: 'Staat het apparaat aan, en heeft het nog hetzelfde IP-adres? Een vaste IP-reservering in de router voorkomt dit.',
  identity_mismatch: 'Op dit adres antwoordt een ander apparaat. Zoek het juiste IP-adres op in de router en wijzig het adres.',
  key_mismatch: 'De opgeslagen sleutel past niet (bijvoorbeeld na herstel op een nieuwe server). Koppel het apparaat opnieuw; de geschiedenis blijft.',
  certificate_mismatch: 'Het certificaat van het apparaat klopt niet met het gekoppelde apparaat. Controleer het adres, of koppel opnieuw.',
  pairing_required: 'Koppel het apparaat opnieuw via ‘Apparaat toevoegen’; de geschiedenis blijft.',
  protocol_error: 'Het apparaat antwoordt anders dan verwacht. Kopieer de diagnose als je hulp vraagt.',
  unsupported: 'Dit model wordt (nog) niet ondersteund.',
  mqtt_disconnected: 'Geen verbinding met de MQTT-server (Mosquitto). Draait die container?',
  mqtt_connecting: 'Verbinding met MQTT wordt gemaakt.',
  bridge_offline: 'Zigbee2MQTT is offline. Zit de USB-dongle erin en draait de Zigbee2MQTT-container?',
  bridge_unknown: 'Nog geen bericht van Zigbee2MQTT ontvangen.',
  stale_devices: 'Sommige sensoren melden zich niet meer. Controleer de batterij of het bereik.',
};

export type Category = 'meters' | 'climate' | 'zigbee' | 'hue' | 'lights' | 'other';
export const CATEGORY_LABEL: Record<Category, string> = {
  meters: 'Energiemeters',
  climate: 'Airco',
  zigbee: 'Zigbee-sensoren',
  hue: 'Hue Bridge en knoppen',
  lights: 'Lampen, groepen en scènes',
  other: 'Overige apparaten',
};
export const CATEGORY_ORDER: Category[] = ['meters', 'climate', 'zigbee', 'hue', 'lights', 'other'];

export function deviceCategory(kind: DeviceKind): Category {
  switch (kind) {
    case 'p1_meter':
    case 'kwh_meter':
    case 'energy_socket':
    case 'meter':
      return 'meters';
    case 'ac_unit':
      return 'climate';
    case 'zigbee_sensor':
    case 'zigbee_device':
    case 'zigbee_bridge':
      return 'zigbee';
    case 'hue_bridge':
    case 'hue_device':
    case 'button':
      return 'hue';
    case 'light':
    case 'light_group':
    case 'scene':
      return 'lights';
    default:
      return 'other';
  }
}

export const kindLabel = (k: string) => t(KIND_LABEL, k);
export const roleLabel = (r: string) => t(ROLE_LABEL, r);

/** "+0,5 °C" style signed number. */
export function signed(v: number, unit: string): string {
  const s = Math.abs(v).toLocaleString('nl-NL', { maximumFractionDigits: 2 });
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${s} ${unit}`;
}

/** "Correctie: temperatuur +0,5 °C, vochtigheid −3 %" or null when there is none. */
export function calibrationText(d: Device): string | null {
  const parts: string[] = [];
  if (d.calibration?.temperature_offset) parts.push(`temperatuur ${signed(d.calibration.temperature_offset, '°C')}`);
  if (d.calibration?.humidity_offset) parts.push(`vochtigheid ${signed(d.calibration.humidity_offset, '%')}`);
  return parts.length ? `Correctie: ${parts.join(', ')}` : null;
}

export const POWER_SOURCE_LABEL: Record<string, string> = { Battery: 'batterij', battery: 'batterij', 'Mains (single phase)': 'netstroom', mains: 'netstroom', DC: 'netstroom' };
export const AVAILABILITY_LABEL: Record<string, string> = { online: 'online', offline: 'offline' };
