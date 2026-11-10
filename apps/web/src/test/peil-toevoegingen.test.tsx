// Toegestane toevoegingen bij de Peil-omzetting (BESLUITEN_GEBRUIKER, docs/ontwerp/PEIL_MIGRATIE.md §9): de
// themaknop Systeem / Licht / Donker (O-09) en de link "Terug naar de Hub" (O-11).
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { geldigHubAdres, HUB_CONFIG_URL, laadHubAdres, vergeetHubAdres } from '../hub';
import { HubLink } from '../components/peil/HubLink';
import { ThemaKeuze } from '../components/peil/ThemaKeuze';
import { leesThema, losThemaOp, pasThemaToe, THEMA_KEY, type ThemaKeuze as Keuze } from '../theme';
import { mockApi } from './helpers';

/** matchMedia voor jsdom: `donker` = de systeeminstelling; `zet` wisselt die en meldt het aan de luisteraars. */
function fakeMedia(donker: boolean) {
  const listeners = new Set<() => void>();
  const mq = {
    get matches() {
      return donker;
    },
    media: '(prefers-color-scheme: dark)',
    addEventListener: (_: string, f: () => void) => listeners.add(f),
    removeEventListener: (_: string, f: () => void) => listeners.delete(f),
    addListener: (f: () => void) => listeners.add(f),
    removeListener: (f: () => void) => listeners.delete(f),
  };
  vi.stubGlobal('matchMedia', () => mq);
  window.matchMedia = (() => mq) as unknown as typeof window.matchMedia;
  return {
    zet(v: boolean) {
      donker = v;
      listeners.forEach((f) => f());
    },
  };
}

function resetHtml() {
  const root = document.documentElement;
  root.removeAttribute('data-theme');
  root.classList.remove('dark');
  root.style.colorScheme = '';
  document.head.innerHTML = '<meta name="theme-color" content="#000000" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#000000" media="(prefers-color-scheme: dark)">';
}

const THEMA_JS = readFileSync(join(__dirname, '../../public/thema.js'), 'utf8');
/** Draait public/thema.js zoals de browser het vóór de eerste weergave doet. */
function draaiThemaScript() {
  new Function(THEMA_JS)();
}

describe('Thema Systeem / Licht / Donker (O-09)', () => {
  beforeEach(() => {
    localStorage.clear();
    resetHtml();
  });

  it('lost het thema op: Systeem volgt het apparaat, Licht en Donker zijn vast', () => {
    expect(losThemaOp('systeem', true)).toBe('dark');
    expect(losThemaOp('systeem', false)).toBe('light');
    expect(losThemaOp('licht', true)).toBe('light');
    expect(losThemaOp('donker', false)).toBe('dark');
    localStorage.setItem(THEMA_KEY, 'onzin');
    expect(leesThema()).toBe('systeem');
  });

  it('public/thema.js zet vóór de eerste weergave hetzelfde thema als de app (zelfde regels, altijd opgelost)', () => {
    for (const keuze of ['systeem', 'licht', 'donker'] as Keuze[]) {
      for (const systeemDonker of [false, true]) {
        fakeMedia(systeemDonker);
        localStorage.clear();
        if (keuze !== 'systeem') localStorage.setItem(THEMA_KEY, keuze);
        resetHtml();
        draaiThemaScript();
        const verwacht = losThemaOp(keuze, systeemDonker);
        expect(document.documentElement.dataset.theme, `${keuze}/${systeemDonker}`).toBe(verwacht);
        expect(document.documentElement.classList.contains('dark')).toBe(verwacht === 'dark');
        const kleuren = [...document.querySelectorAll('meta[name="theme-color"]')].map((m) => m.getAttribute('content'));
        expect(kleuren).toEqual(verwacht === 'dark' ? ['#0A0D12', '#0A0D12'] : ['#EEF1F5', '#EEF1F5']);
        resetHtml();
        expect(pasThemaToe(keuze)).toBe(verwacht);
        expect(document.documentElement.dataset.theme).toBe(verwacht);
      }
    }
  });

  it('kiezen bewaart de keuze op dit apparaat, wisselt zonder herladen en houdt de focus op de knop', async () => {
    const media = fakeMedia(false);
    const user = userEvent.setup();
    render(<ThemaKeuze />);
    const groep = screen.getByRole('group', { name: 'Thema' });
    expect(groep).toHaveAccessibleDescription('Systeem volgt de instelling van je apparaat.');
    const knoppen = within(groep).getAllByRole('button');
    expect(knoppen.map((b) => b.textContent)).toEqual(['Systeem', 'Licht', 'Donker']);
    expect(within(groep).getByRole('button', { name: 'Systeem' })).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement.dataset.theme).toBe('light');

    // Systeem volgt een wijziging van de systeeminstelling.
    act(() => media.zet(true));
    expect(document.documentElement.dataset.theme).toBe('dark');

    await user.click(within(groep).getByRole('button', { name: 'Licht' }));
    expect(localStorage.getItem(THEMA_KEY)).toBe('licht');
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(within(groep).getByRole('button', { name: 'Licht' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(groep).getByRole('button', { name: 'Licht' })).toHaveFocus();

    await user.click(within(groep).getByRole('button', { name: 'Donker' }));
    expect(localStorage.getItem(THEMA_KEY)).toBe('donker');
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);

    // Terug naar Systeem: geen opgeslagen keuze meer (thema.js leest dat ook als Systeem).
    await user.click(within(groep).getByRole('button', { name: 'Systeem' }));
    expect(localStorage.getItem(THEMA_KEY)).toBeNull();
    expect(document.documentElement.dataset.theme).toBe('dark');
  });
});

describe('Terug naar de Hub (O-11)', () => {
  afterEach(() => vergeetHubAdres());

  it('accepteert alleen http(s)://host[:poort] zonder pad, query, fragment of inloggegevens, en geen loopback/link-local', () => {
    expect(geldigHubAdres('https://hub.thuis:8443')).toBe('https://hub.thuis:8443');
    expect(geldigHubAdres('https://hub.thuis:8443/')).toBe('https://hub.thuis:8443');
    expect(geldigHubAdres('http://192.168.1.5')).toBe('http://192.168.1.5');
    expect(geldigHubAdres('https://[2001:db8::1]:8443')).toBe('https://[2001:db8::1]:8443');
    for (const bad of [
      undefined,
      null,
      42,
      '',
      'javascript:alert(1)',
      'data:text/html,x',
      'ftp://hub.thuis',
      'hub.thuis',
      '//hub.thuis',
      'https://hub.thuis/pad',
      'https://hub.thuis/?x=1',
      'https://hub.thuis#x',
      'https://user:pass@hub.thuis',
      'https://hub.thuis\\@evil',
      'http://localhost:8443',
      'http://app.localhost',
      'http://127.0.0.1',
      'http://127.8.9.1:80',
      'http://0.0.0.0',
      'http://169.254.169.254',
      'http://[::1]',
      'http://[::]',
      'http://[fe80::1]',
      'http://[::ffff:127.0.0.1]',
      // Andere schrijfwijzen van dezelfde adressen: de URL-parser maakt er 127.0.0.1, ::1 of :: van.
      'http://0177.0.0.1:8443',
      'http://2130706433',
      'http://0x7f.1',
      'http://127.1',
      'http://169.254.0x1.1',
      'http://[0::1]',
      'http://[0:0:0:0:0:0:0:1]',
      'http://[0::]',
      'http://[0:0:0:0:0:ffff:7f00:1]',
    ]) {
      expect(geldigHubAdres(bad), String(bad)).toBeNull();
    }
  });

  // Contract 1.3 §3.2: dezelfde gevallen als het startscript van de webcontainer (infra/tests/test_scripts.py), zodat
  // de tweede controle in de browser nooit iets doorlaat dat het script weigert, en andersom.
  const gevallen = JSON.parse(readFileSync(join(__dirname, '../../../../infra/tests/hub_url_gevallen.json'), 'utf8')) as {
    geldig: [string, string][];
    ongeldig: string[];
  };

  it.each(gevallen.geldig)('contract 1.3 §3.2: %s is geldig', (invoer, origin) => {
    expect(geldigHubAdres(invoer)).toBe(new URL(origin).origin);
    // Wat het startscript schrijft, is zelf ook geldig en verandert niet meer.
    expect(geldigHubAdres(origin)).toBe(new URL(origin).origin);
  });

  it.each(gevallen.ongeldig)('contract 1.3 §3.2: %s is ongeldig (geen link)', (invoer) => {
    expect(geldigHubAdres(invoer)).toBeNull();
  });

  it('leest het adres alleen uit /_shc/config.json; geen bestand of geen JSON = geen link', async () => {
    const calls = mockApi((_m, url) => (url === HUB_CONFIG_URL ? { body: { hub_url: 'https://hub.thuis:8443' } } : undefined));
    expect(await laadHubAdres()).toBe('https://hub.thuis:8443');
    expect(calls.map((c) => c.url)).toEqual([HUB_CONFIG_URL]);
    vergeetHubAdres();
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<!doctype html>', { status: 200, headers: { 'content-type': 'text/html' } })));
    expect(await laadHubAdres()).toBeNull();
    vergeetHubAdres();
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline'); }));
    expect(await laadHubAdres()).toBeNull();
  });

  it('toont de link (zelfde tabblad) met een geldig adres en is verborgen zonder of met een ongeldig adres', async () => {
    mockApi((_m, url) => (url === HUB_CONFIG_URL ? { body: { hub_url: 'https://hub.thuis:8443' } } : undefined));
    const { unmount } = render(<HubLink />);
    const link = await screen.findByRole('link', { name: 'Terug naar de Hub' });
    expect(link).toHaveAttribute('href', 'https://hub.thuis:8443');
    expect(link).not.toHaveAttribute('target');
    unmount();

    for (const body of [{}, { hub_url: 'javascript:alert(1)' }, { hub_url: 'https://hub.thuis/pad' }, { hub_url: 'http://127.0.0.1:8443' }]) {
      vergeetHubAdres();
      mockApi((_m, url) => (url === HUB_CONFIG_URL ? { body } : undefined));
      const r = render(<HubLink />);
      await waitFor(() => expect(fetch).toHaveBeenCalled());
      await act(async () => {});
      expect(screen.queryByRole('link', { name: 'Terug naar de Hub' })).not.toBeInTheDocument();
      r.unmount();
    }
  });
});

