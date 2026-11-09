// Thema Systeem / Licht / Donker (O-09; docs/ontwerp/PEIL_MIGRATIE.md §9.2). Alleen uiterlijk en alleen op
// dit apparaat (localStorage), geen serverinstelling. public/thema.js zet het thema vóór de eerste weergave
// met dezelfde regels; deze module doet het na een keuze in de app (zonder herladen).
import { useCallback, useEffect, useState } from 'react';

export type ThemaKeuze = 'systeem' | 'licht' | 'donker';
export type OpgelostThema = 'light' | 'dark';

export const THEMA_KEY = 'shc.thema';
export const THEMA_KEUZES: { key: ThemaKeuze; label: string }[] = [
  { key: 'systeem', label: 'Systeem' },
  { key: 'licht', label: 'Licht' },
  { key: 'donker', label: 'Donker' },
];
/** theme-color van de PWA per opgelost thema (Peil background). */
export const THEMA_KLEUR: Record<OpgelostThema, string> = { light: '#EEF1F5', dark: '#0A0D12' };

function media(): MediaQueryList | null {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: dark)') : null;
}

export function leesThema(): ThemaKeuze {
  try {
    const v = window.localStorage.getItem(THEMA_KEY);
    return v === 'licht' || v === 'donker' ? v : 'systeem';
  } catch {
    return 'systeem';
  }
}

export function losThemaOp(keuze: ThemaKeuze, systeemDonker = Boolean(media()?.matches)): OpgelostThema {
  return keuze === 'donker' || (keuze === 'systeem' && systeemDonker) ? 'dark' : 'light';
}

/** Zet het opgeloste thema op <html> (data-theme + klasse dark + color-scheme) en de theme-color. */
export function pasThemaToe(keuze: ThemaKeuze = leesThema()): OpgelostThema {
  const thema = losThemaOp(keuze);
  const root = document.documentElement;
  root.setAttribute('data-theme', thema);
  root.classList.toggle('dark', thema === 'dark');
  root.style.colorScheme = thema;
  document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.setAttribute('content', THEMA_KLEUR[thema]));
  return thema;
}

export function bewaarThema(keuze: ThemaKeuze): void {
  try {
    if (keuze === 'systeem') window.localStorage.removeItem(THEMA_KEY);
    else window.localStorage.setItem(THEMA_KEY, keuze);
  } catch {
    // Geen opslag (privévenster): de keuze geldt dan alleen tot herladen.
  }
}

/** Huidige keuze + kiezen. Volgt een wijziging van de systeeminstelling zolang "Systeem" gekozen is. */
export function useThema(): [ThemaKeuze, (k: ThemaKeuze) => void] {
  const [keuze, setKeuze] = useState<ThemaKeuze>(() => leesThema());
  useEffect(() => {
    pasThemaToe(keuze);
    const mq = media();
    if (keuze !== 'systeem' || !mq) return;
    const volg = () => pasThemaToe('systeem');
    mq.addEventListener?.('change', volg);
    return () => mq.removeEventListener?.('change', volg);
  }, [keuze]);
  const kies = useCallback((k: ThemaKeuze) => {
    bewaarThema(k);
    setKeuze(k);
  }, []);
  return [keuze, kies];
}
