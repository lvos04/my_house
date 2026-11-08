import type { Quality } from './api/types';

const nl = 'nl-NL';

export function temp(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return '–';
  return v.toLocaleString(nl, { minimumFractionDigits: digits, maximumFractionDigits: digits }) + '°';
}
export function kwh(v: number | null | undefined): string {
  if (v === null || v === undefined) return '– kWh';
  return v.toLocaleString(nl, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' kWh';
}
export function watts(v: number | null | undefined): string {
  if (v === null || v === undefined) return '– W';
  return Math.round(v).toLocaleString(nl) + ' W';
}
export function money(v: number | null | undefined, currency = 'EUR'): string {
  if (v === null || v === undefined) return '';
  return v.toLocaleString(nl, { style: 'currency', currency });
}
export function time(ts: string | Date | null | undefined, tz?: string): string {
  if (!ts) return '–';
  const d = typeof ts === 'string' ? new Date(ts) : ts;
  return d.toLocaleTimeString(nl, { hour: '2-digit', minute: '2-digit', timeZone: tz });
}
export function dateTime(ts: string | null | undefined, tz?: string): string {
  if (!ts) return '–';
  return new Date(ts).toLocaleString(nl, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: tz });
}
export function ago(ts: string | null | undefined, now: number = Date.now()): string {
  if (!ts) return 'nooit';
  const s = Math.max(0, Math.round((now - new Date(ts).getTime()) / 1000));
  if (s < 60) return 'zojuist';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min geleden`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} uur geleden`;
  return `${Math.round(h / 24)} dagen geleden`;
}

export const QUALITY_LABEL: Record<Quality, string> = {
  measured: 'gemeten',
  derived: 'afgeleid',
  estimated: 'geschat',
  missing: 'ontbreekt',
};

export const MODE_LABEL: Record<string, string> = {
  off: 'Uit',
  heat: 'Verwarmen',
  cool: 'Koelen',
  auto: 'Auto',
  dry: 'Drogen',
  fan: 'Ventileren',
};

export const CONTROL_LABEL: Record<string, string> = {
  schedule: 'Schema',
  manual: 'Handmatig',
  oem_fallback: 'OEM-regeling',
  off: 'Uit',
};

export function localDate(d: Date = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}
