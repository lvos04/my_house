import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Backup, BackupSettings, RetentionSettings, SystemStatus } from '../api/types';
import { SystemPage } from '../pages/System';
import { InstallPage, INSTALL_KEY } from '../pages/Install';
import { HelpPage } from '../pages/Help';
import { mockApi, Providers, viewer } from './helpers';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

const status: SystemStatus = {
  version: '0.2.0',
  checks: [
    { key: 'database', label: 'Database', state: 'ok', detail: 'Schema 0003_followups' },
    { key: 'worker', label: 'Achtergronddienst', state: 'ok', detail: 'Laatste hartslag 12 s geleden', age_s: 12 },
    { key: 'backup', label: 'Back-up', state: 'warn', detail: 'Laatste back-up 2 uur geleden', age_h: 2, app_version: '0.2.0', schema_revision: '0003_followups', zigbee2mqtt_included: false, auto_failures: 0, auto_last_error: null },
    {
      key: 'integration:i1', label: 'Gree airco', state: 'warn', detail: 'Antwoordt traag', integration_id: 'i1', provider: 'gree', code: null,
      details: { code: null, since: null, health_state: 'warn', health_detail: 'Antwoordt traag', checked_at: null, connected_since: null, missed_polls: 2, consecutive_failures: 1, extra: { host: '192.168.1.40', encryption: 'v2' } },
    },
    { key: 'integration:i2:mqtt', label: 'Zigbee – MQTT-verbinding', state: 'ok', detail: 'Verbonden' },
  ],
};
const settings: BackupSettings = {
  enabled: false, wanted: false, passphrase_set: false, passphrase_source: null, passphrase_error: null, hour: 3, keep: 14, timezone: 'Europe/Amsterdam',
  last_auto_backup: null, next_due_at: null, due_now: false, last_error: null, failures: 0, retry_after: null, last_backup: null, zigbee2mqtt_in_last_backup: null,
};
const backup = (over: Partial<Backup>): Backup => ({
  id: 'b1', created_at: '2026-11-08T03:00:00Z', kind: 'export', auto: true, filename: 'shc-auto-1.shcbak', size_bytes: 1_048_576, sha256: 'abc', checksum: 'abc',
  status: 'ok', app_version: '0.2.0', schema_revision: '0003_followups', secrets_included: true, zigbee2mqtt_included: true, error: null, detail: {}, downloadable: true, ...over,
});
const retention: RetentionSettings = {
  power_days: 30, counter_days: 365, log_days: 90, source: { power_days: 'env', counter_days: 'env', log_days: 'env' },
  defaults: { power_days: 30, counter_days: 365, log_days: 90 }, minimums: { power_days: 7, counter_days: 90, log_days: 7 }, maximum: 3650,
  note: 'Uurtotalen van energie en gas worden altijd bewaard.',
};

function systemRoutes(m: string, url: string, body: unknown) {
  if (url.endsWith('/system/status')) return { body: status };
  if (url.endsWith('/system/settings')) return { body: { lan_url: null, remote_url: null, safe_mode: false, maintenance: false } };
  if (url.endsWith('/system/backup-settings') && m === 'GET') return { body: settings };
  if (url.endsWith('/system/backup-settings') && m === 'PUT') return { body: { ...settings, ...(body as object), passphrase_set: true, passphrase_source: 'app' } };
  if (url.endsWith('/system/backups')) return { body: [backup({}), backup({ id: 'b2', auto: false, status: 'failed', downloadable: false, error: 'Schijf vol', filename: 'x.shcbak' })] };
  if (url.endsWith('/system/retention') && m === 'GET') return { body: retention };
  if (url.endsWith('/system/retention') && m === 'PUT') return { body: { ...retention, power_days: 60 } };
  if (url.endsWith('/homes')) return { body: [{ id: 'h1', name: 'Thuis', timezone: 'Europe/Amsterdam', currency: 'EUR', active: true }] };
  return undefined;
}

describe('Systeem (items 22/28/32/34)', () => {
  it('shows checks with details, versions, backup settings, list and retention', async () => {
    const calls = mockApi(systemRoutes);
    render(<Providers route="/systeem"><SystemPage /></Providers>);
    const gree = await screen.findByTestId('check-integration:i1');
    const user = userEvent.setup();
    await user.click(within(gree).getByText('Details'));
    expect(within(gree).getByText('Gemiste uitleesrondes')).toBeInTheDocument();
    expect(within(gree).getByText('v2')).toBeInTheDocument();
    const bk = screen.getByTestId('check-backup');
    await user.click(within(bk).getByText('Details'));
    expect(within(bk).getByText('Zigbee in laatste back-up')).toBeInTheDocument();
    expect(within(bk).getByText('app 0.2.0 · schema 0003_followups')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Koppelingen' })).toBeInTheDocument();
    const ver = screen.getByRole('region', { name: 'Versie' });
    expect(within(ver).getByText('0003_followups')).toBeInTheDocument();

    // automatic backups: enabling needs a passphrase first
    await user.click(await screen.findByRole('checkbox', { name: 'Automatische back-up aan' }));
    await user.click(within(screen.getByRole('form', { name: 'Instellingen automatische back-ups' })).getByRole('button', { name: 'Opslaan' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Stel eerst een wachtwoordzin in');
    await user.type(screen.getByLabelText('Wachtwoordzin (min. 12 tekens)', { selector: 'form[aria-label="Instellingen automatische back-ups"] input' }), 'een-lange-zin-123');
    await user.type(screen.getByLabelText('Herhaal wachtwoordzin'), 'een-lange-zin-123');
    await user.selectOptions(screen.getByLabelText(/Tijdstip/), '4');
    await user.click(within(screen.getByRole('form', { name: 'Instellingen automatische back-ups' })).getByRole('button', { name: 'Opslaan' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT' && c.url.endsWith('/backup-settings'))?.body).toEqual({ passphrase: 'een-lange-zin-123', enabled: true, hour: 4 }));

    // list: automatic vs manual, download only when possible
    const auto = (await screen.findByText('shc-auto-1.shcbak')).closest('tr')!;
    expect(within(auto).getByText('Automatisch')).toBeInTheDocument();
    expect(within(auto).getByRole('link', { name: /downloaden/ })).toHaveAttribute('href', '/api/v1/system/backups/b1/download');
    const failed = screen.getByText('x.shcbak').closest('tr')!;
    expect(within(failed).getByText('Handmatig')).toBeInTheDocument();
    expect(within(failed).getByText('Schijf vol')).toBeInTheDocument();
    expect(within(failed).queryByRole('link')).not.toBeInTheDocument();

    // retention
    const power = await screen.findByLabelText('Vermogen (detail) (dagen)');
    await user.clear(power);
    await user.type(power, '60');
    await user.click(within(screen.getByRole('form', { name: 'Bewaartermijnen' })).getByRole('button', { name: 'Opslaan' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT' && c.url.endsWith('/system/retention'))?.body).toEqual({ power_days: 60 }));
  });

  it('viewers see status and logout, not the admin sections', async () => {
    mockApi(systemRoutes);
    render(<Providers user={viewer}><SystemPage /></Providers>);
    expect(await screen.findByTestId('check-worker')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Adressen' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: /Herstellen en verhuizen/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Uitloggen' })).toBeInTheDocument();
  });
});

describe('installation wizard (item 43)', () => {
  it('starts with preflight, saves progress, and shows Gree instructions before pairing', async () => {
    const calls = mockApi((m, url) => {
      if (url.endsWith('/system/preflight'))
        return { body: { checked_at: '2026-11-08T10:00:00Z', ready: false, checks: [{ key: 'home', label: 'Woning', state: 'error', detail: 'Maak eerst een woning aan (naam en tijdzone).' }, { key: 'discovery', label: 'Netwerkdetectie', state: 'warn', detail: 'Niet beschikbaar in het containernetwerk' }] } };
      if (url.endsWith('/integrations/gree/instructions'))
        return { body: { provider: 'gree', label: 'Gree airco', steps: ['Zet de unit in koppelmodus'], needs_manufacturer_app: 'Eenmalig wifi-koppeling via de Gree+-app (2,4 GHz).', works_locally: 'Aan/uit, modus' } };
      if (url.endsWith('/integrations/gree/pair') && m === 'POST')
        return { status: 502, body: { detail: { code: 'unsupported', message: 'Koppelen mislukt: deze module wordt niet ondersteund.', diagnostics: { stage: 'bind', mac: 'aa:bb', bind_attempts: [{ encryption: 'v1', result: 'geen antwoord' }] }, integration_id: 'i9', device_id: 'd9' } } };
      if (url.endsWith('/integrations')) return { body: [] };
      return undefined;
    });
    render(<Providers route="/installatie"><InstallPage /></Providers>);
    expect(await screen.findByText('Maak eerst een woning aan (naam en tijdzone).')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Controles vooraf/ })).toHaveAttribute('aria-current', 'step');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Volgende' }));
    expect(JSON.parse(localStorage.getItem(INSTALL_KEY)!)).toEqual({ step: 'woning', done: ['controle'] });

    await user.click(screen.getByRole('button', { name: /Gree airco/ }));
    expect(await screen.findByText(/Eenmalig wifi-koppeling via de Gree\+-app/)).toBeInTheDocument();
    expect(screen.getByText('Zet de unit in koppelmodus')).toBeInTheDocument();
    expect(screen.queryByLabelText('IP-adres van de airco-unit')).not.toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', { name: /met de Gree\+-app aan mijn 2,4 GHz-wifi gekoppeld/ }));
    await user.type(screen.getByLabelText('IP-adres van de airco-unit'), '192.168.1.40');
    await user.click(screen.getByRole('button', { name: 'Koppelen' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('deze module wordt niet ondersteund');
    expect(screen.getByText(/"bind_attempts"/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Diagnose kopiëren' }));
    expect(await navigator.clipboard.readText()).toContain('"mac": "aa:bb"');
    expect(screen.getByText('Gekopieerd.')).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith('/gree/pair'))?.body).toEqual({ home_id: 'h1', host: '192.168.1.40' });
  });

  it('restores saved progress', async () => {
    localStorage.setItem(INSTALL_KEY, JSON.stringify({ step: 'klaar', done: ['controle', 'woning'] }));
    mockApi(() => undefined);
    render(<Providers route="/installatie"><InstallPage /></Providers>);
    expect(screen.getByRole('heading', { level: 2, name: 'Klaar' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Woning \(gedaan\)/ })).toBeInTheDocument();
  });
});

describe('help page', () => {
  it('shows per manufacturer what works locally, what needs the vendor app, steps with photo slots and the matrix', async () => {
    mockApi((_m, url) => {
      if (url.endsWith('/integrations/compatibility'))
        return { body: { providers: [], note: '', rows: [{ provider: 'hue', model: 'Bridge v2 (BSB002)', protocol: 'CLIP v2', local: 'Lampen', vendor_app: 'Lampen eerst in de Hue-app', status: 'tested', devices: [{ id: 'd1', name: 'Bridge', model: 'BSB002', compatibility: 'tested', tested_at: null }], counts: { tested: 1, untested: 0, unsupported: 0 } }] } };
      return undefined;
    });
    render(<Providers route="/help"><HelpPage /></Providers>);
    const hue = screen.getByRole('region', { name: 'Philips Hue' });
    expect(within(hue).getByRole('heading', { name: 'Wat lokaal werkt' })).toBeInTheDocument();
    expect(within(hue).getByRole('heading', { name: 'Eerst in de app van de fabrikant' })).toBeInTheDocument();
    expect(within(hue).getAllByRole('img', { name: /Foto volgt/ }).length).toBeGreaterThan(0);
    expect(await within(hue).findByText('Bridge v2 (BSB002)')).toBeInTheDocument();
    expect(within(hue).getByText('getest')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Gree airco' })).toBeInTheDocument();
  });
});
