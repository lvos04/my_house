// "Terug naar de Hub" (O-11; docs/ontwerp/PEIL_MIGRATIE.md §9.1, ADR-0008 punt 3). Het adres komt alleen
// uit de configuratie van de app: de webserver biedt de optionele omgevingsvariabele HUB_URL aan als
// /_shc/config.json ({"hub_url": "…"}). Nooit uit de URL, document.referrer of invoer van de gebruiker.
// Onbekend of ongeldig = geen link (geen grijze knop, geen plaatshouder).
import { useEffect, useState } from 'react';

export const HUB_CONFIG_URL = '/_shc/config.json';

function isLoopbackOrLinkLocal(host: string): boolean {
  const h = host.toLowerCase().replace(/^\[|\]$/g, '');
  if (h === 'localhost' || h.endsWith('.localhost')) return true;
  if (h === '0.0.0.0' || h === '::' || h === '::1' || h === '0:0:0:0:0:0:0:1') return true;
  const v4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(h);
  if (v4) {
    const [a, b] = [Number(v4[1]), Number(v4[2])];
    if (a === 127 || a === 0) return true; // loopback, "dit netwerk"
    if (a === 169 && b === 254) return true; // link-local
  }
  if (/^fe[89ab][0-9a-f]?:/.test(h)) return true; // fe80::/10
  if (h.startsWith('::ffff:')) return true; // IPv4-gemapt (URL schrijft het als hex): nooit nodig, dus weigeren
  return false;
}

/**
 * Het Hub-adres als het voldoet aan de regels van HUB_URL (contract 1.2 §3.2): http(s)://<host>[:<poort>]
 * zonder pad, query, fragment of inloggegevens; geen loopback, link-local of 0.0.0.0/::. Anders null.
 * Eén afsluitende "/" mag. Geeft de genormaliseerde origin terug.
 */
export function geldigHubAdres(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const s = raw.trim();
  if (!s || s.length > 2048 || /[\s\\]/.test(s)) return null;
  if (!/^https?:\/\/[^/?#]+\/?$/i.test(s)) return null; // alleen schema + host[:poort], hooguit één "/"
  let u: URL;
  try {
    u = new URL(s);
  } catch {
    return null;
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
  if (u.username || u.password || s.includes('@')) return null;
  if (u.pathname !== '/' || u.search || u.hash) return null;
  if (!u.hostname || isLoopbackOrLinkLocal(u.hostname)) return null;
  return u.origin;
}

let cached: Promise<string | null> | null = null;

/** Leest /_shc/config.json één keer per paginalading. Elke fout (geen bestand, geen JSON) = null. */
export function laadHubAdres(): Promise<string | null> {
  if (!cached) {
    cached = (async () => {
      try {
        const res = await fetch(HUB_CONFIG_URL, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
        if (!res.ok || !(res.headers.get('content-type') ?? '').includes('application/json')) return null;
        const body: unknown = await res.json();
        if (!body || typeof body !== 'object' || Array.isArray(body)) return null;
        return geldigHubAdres((body as Record<string, unknown>).hub_url);
      } catch {
        return null;
      }
    })();
  }
  return cached;
}

/** Alleen voor tests: vergeet het geladen adres. */
export function vergeetHubAdres(): void {
  cached = null;
}

export function useHubAdres(): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let actief = true;
    laadHubAdres().then((u) => {
      if (actief) setUrl(u);
    });
    return () => {
      actief = false;
    };
  }, []);
  return url;
}
