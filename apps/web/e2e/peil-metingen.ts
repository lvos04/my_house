// Metingen van de Peil-browsertests (docs/ontwerp/PEIL_MIGRATIE.md §12, PEIL_REVIEW.md), gedeeld door
// 11-peil (hoofdschermen) en 12-grafiek-24-uur (tabel en toetsenbord bij "Laatste 24 uur"). Geen testbestand: de
// naam eindigt niet op .e2e.ts.
import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { expect } from './helpers';

export const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

/** Wacht tot de pagina klaar is met laden (één h1, geen "Laden…"). */
export async function settled(page: Page) {
  await expect(page.locator('main h1, .auth h1').first()).toBeVisible();
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0, { timeout: 15_000 });
}

export async function axe(page: Page, what: string) {
  const result = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  const found = result.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(' ')).slice(0, 5).join(' | ')}`);
  expect(found, `axe ${what}`).toEqual([]);
}

/** Tekst kleiner dan 14 px (alleen zichtbare elementen met eigen tekst; sr-only telt niet mee). */
export async function smallText(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('body *'))) {
      const own = Array.from(el.childNodes).some((n) => n.nodeType === Node.TEXT_NODE && n.textContent!.trim());
      if (!own) continue;
      const r = el.getBoundingClientRect();
      if (r.width <= 1 || r.height <= 1) continue; // sr-only en verborgen
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none') continue;
      if (parseFloat(cs.fontSize) < 13.95) out.push(`${el.tagName.toLowerCase()}.${el.className}: ${cs.fontSize} "${el.textContent!.trim().slice(0, 30)}"`);
    }
    return out;
  });
}

/** Bedienbare doelen kleiner dan 44 × 44 px. Uitzondering (WCAG 2.5.8): links in lopende tekst. Een
 * checkbox of radio telt met zijn label-rij. */
export async function smallTargets(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    const sel = 'button, a[href], input:not([type="hidden"]), select, textarea, summary, [role="tab"], [role="button"]';
    for (const el of Array.from(document.querySelectorAll<HTMLElement>(sel))) {
      let target: HTMLElement = el;
      if (el instanceof HTMLInputElement && (el.type === 'checkbox' || el.type === 'radio')) target = el.closest('label') ?? el;
      const r = target.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue; // niet zichtbaar
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden') continue;
      if (el.closest('.sr-only, .skip-link')) continue;
      if (el.tagName === 'A' && cs.display === 'inline') {
        const p = el.parentElement;
        const inText = p && Array.from(p.childNodes).some((n) => n !== el && n.nodeType === Node.TEXT_NODE && n.textContent!.trim());
        if (inText) continue;
      }
      if (r.width < 43.5 || r.height < 43.5) out.push(`${el.tagName.toLowerCase()} "${(el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 30)}": ${Math.round(r.width)}×${Math.round(r.height)}`);
    }
    return out;
  });
}

/** Bedienbare elementen of blokken die rechts buiten hun kaart steken (geen zijwaartse scroll, maar wel
 * kapot). Tabellen in .table-wrap mogen scrollen en tellen niet mee. */
export async function outsideCards(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    for (const card of Array.from(document.querySelectorAll<HTMLElement>('main .card'))) {
      const c = card.getBoundingClientRect();
      if (!c.width) continue;
      for (const el of Array.from(card.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href], .sched-block, .card'))) {
        if (el.closest('.table-wrap, [role="img"], .sr-only')) continue;
        const r = el.getBoundingClientRect();
        if (r.width && r.right > c.right + 1) out.push(`${el.tagName.toLowerCase()} "${(el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 30)}" tot ${Math.round(r.right)} (kaart tot ${Math.round(c.right)})`);
      }
    }
    return out;
  });
}

/** Inhoud die buiten zijn eigen knop, tab, tegel of pil steekt (review stap 5: "✓ Verwarmen" stak 10 px uit de
 * modusknop, "Woonkamer" 18 px uit de lichttegel op 320 px). sr-only tekst telt niet. Sinds Peil 1.1.1 (E16: items op
 * labelbreedte, actief zonder extra gewicht) telt ook de ondernavigatie mee; in 1.1.0 stak een vet "Overzicht" op
 * 320 px 2 px uit zijn kolom van 64 px (PEIL_REVIEW.md, open punt). */
export async function contentOutside(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('main button, main a.btn, main [role="tab"], main .light-tile, main .pill, [role="dialog"] button, nav.bottom-nav .bottom-link'))) {
      const r = el.getBoundingClientRect();
      if (!r.width || el.closest('.sr-only')) continue;
      let uit = el.scrollWidth - el.clientWidth;
      for (const k of Array.from(el.querySelectorAll<HTMLElement>('*'))) {
        if (k.closest('.sr-only')) continue;
        const kr = k.getBoundingClientRect();
        if (kr.width) uit = Math.max(uit, kr.right - r.right, r.left - kr.left);
      }
      if (uit > 1) out.push(`${el.tagName.toLowerCase()} "${(el.textContent || '').trim().slice(0, 30)}" steekt ${Math.round(uit)} px uit`);
    }
    return out;
  });
}

/** De focus van de huidige stop: ring aanwezig (≥ 2 px), contrast van de ring met het vlak eromheen, en of het
 * element helemaal achter een vaste balk ligt (WCAG 2.4.7, 2.4.11, Peil §12). */
export async function focusStop(page: Page) {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body) return null;
    const naam = `${el.tagName.toLowerCase()} "${(el.innerText || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 30)}"`;
    // Het klok-/kalenderknopje in een tijd- of datumveld tekent zijn eigen ring (browser); dan matcht :focus niet.
    if (!el.matches(':focus')) return { naam, browserdeel: true, ring: 0, contrast: 0, bedekt: false };
    const cs = getComputedStyle(el);
    const c = document.createElement('canvas').getContext('2d')!;
    const rgb = (s: string) => { c.clearRect(0, 0, 1, 1); c.fillStyle = '#000'; c.fillStyle = s; c.fillRect(0, 0, 1, 1); return Array.from(c.getImageData(0, 0, 1, 1).data); };
    const lum = (v: number[]) => { const f = (x: number) => { x /= 255; return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }; return 0.2126 * f(v[0]) + 0.7152 * f(v[1]) + 0.0722 * f(v[2]); };
    let bg = [255, 255, 255, 255];
    for (let p = el.parentElement; p; p = p.parentElement) { const b = rgb(getComputedStyle(p).backgroundColor); if (b[3] === 255) { bg = b; break; } }
    const ring = cs.outlineStyle === 'none' ? 0 : parseFloat(cs.outlineWidth);
    const a = lum(rgb(cs.outlineColor)), b = lum(bg);
    const contrast = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    const r = el.getBoundingClientRect();
    const pts = [[r.left + r.width / 2, r.top + r.height / 2], [r.left + 6, r.top + 6], [r.right - 6, r.bottom - 6], [r.left + 6, r.bottom - 6], [r.right - 6, r.top + 6]]
      .filter(([x, y]) => x >= 0 && y >= 0 && x < innerWidth && y < innerHeight);
    const zichtbaar = pts.filter(([x, y]) => { const t = document.elementFromPoint(x, y); return !!t && (t === el || el.contains(t) || t.contains(el)); }).length;
    return { naam, browserdeel: false, ring, contrast, bedekt: pts.length > 0 && zichtbaar === 0 };
  });
}

/** Het element dat het verst buiten de pagina steekt (diagnose bij zijwaartse scroll). */
export async function widest(page: Page): Promise<string> {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    let worst: { el: Element; right: number } | null = null;
    for (const el of Array.from(document.querySelectorAll('body *'))) {
      const r = el.getBoundingClientRect();
      if (r.width && r.right > vw + 1 && (!worst || r.right > worst.right)) worst = { el, right: r.right };
    }
    if (!worst) return 'onbekend';
    const e = worst.el as HTMLElement;
    return `${e.tagName.toLowerCase()}.${String(e.className).split(' ').join('.')} "${(e.textContent ?? '').trim().slice(0, 40)}" tot ${Math.round(worst.right)} px`;
  });
}
