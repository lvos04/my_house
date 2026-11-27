import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { RestoreCompare, RestoreJob, RestoreVerify } from '../api/types';
import { CompareView, droppedLines, RestoreSection } from '../pages/system/Restore';
import { PendingRestoreNotice, RESTORE_KEY } from '../pages/system/restoreState';
import { FakeXhr, mockApi, Providers } from './helpers';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

it('houdt de tussenexport en de afgesloten bronregistratie herkenbaar bij het downloaden', async () => {
  const user = userEvent.setup();
  const pause = { last_reading: 2, reading_count: 2, expires: '2026-11-01T12:00:00Z', stopped_at: null as string | null };
  const keuzes: boolean[] = [];
  mockApi((_method, url, body) => {
    if (url.endsWith('/system/settings')) return { body: { source_pause: pause } };
    if (url.endsWith('/source/export')) {
      const { stop_source } = body as { stop_source: boolean };
      keuzes.push(stop_source);
      if (stop_source) pause.stopped_at = '2026-11-01T11:05:00Z';
      return { body: { file_id: stop_source ? 'eindexport' : 'tussenexport', source_pause: pause } };
    }
  });
  render(<Providers><RestoreSection safeMode={false} /></Providers>);
  await user.click(screen.getByText('Definitieve export op de bron'));
  const veld = await screen.findByLabelText('Wachtwoordzin voor de export');
  await user.type(veld, 'synthetische proefzin');
  await user.click(screen.getByRole('button', { name: 'Tussenexport maken' }));
  expect(await screen.findByRole('link', { name: 'Tussenexport downloaden' })).toHaveAttribute('href', expect.stringContaining('/tussenexport'));
  await user.type(veld, 'synthetische proefzin');
  await user.click(screen.getByRole('button', { name: 'Bronregistratie afsluiten en exporteren' }));
  expect(await screen.findByRole('link', { name: 'Definitieve export downloaden' })).toHaveAttribute('href', expect.stringContaining('/eindexport'));
  expect(await screen.findByText(/Bronregistratie afgesloten op/)).toBeInTheDocument();
  expect(keuzes).toEqual([false, true]);
});

it('biedt de gepubliceerde eindexport na het heropenen van het scherm aan', async () => {
  const pause = { stopped_at: '2026-11-01T11:05:00Z', expires: '2026-11-01T12:00:00Z',
    last_reading: 2, reading_count: 2, file_id: 'bewaarde-export' };
  mockApi((_method, url) => url.endsWith('/system/settings') ? { body: { source_pause: pause } } : undefined);
  render(<Providers><RestoreSection safeMode={false} /></Providers>);
  await userEvent.setup().click(screen.getByText('Definitieve export op de bron'));
  expect(await screen.findByRole('link', { name: 'Definitieve export downloaden' }))
    .toHaveAttribute('href', expect.stringContaining('/bewaarde-export'));
  const terugval = screen.getByText('shc-api prepare-connector-fallback').closest('p');
  expect(terugval).toHaveTextContent('laat de oude worker uit');
  expect(terugval).toHaveTextContent('op die kopie pas na exitcode 0');
});

it('biedt de eerdere tussenexport aan als de archiefmap de eindexport nog niet heeft bevestigd', async () => {
  const pause = { stopped_at: '2026-11-01T11:05:00Z', expires: '2026-11-01T12:00:00Z',
    last_reading: 2, reading_count: 2, interim_file_id: 'eerste-export' };
  mockApi((_method, url) => url.endsWith('/system/settings') ? { body: { source_pause: pause } } : undefined);
  render(<Providers><RestoreSection safeMode={false} /></Providers>);
  await userEvent.setup().click(screen.getByText('Definitieve export op de bron'));
  expect(await screen.findByRole('link', { name: 'Tussenexport downloaden' }))
    .toHaveAttribute('href', expect.stringContaining('/eerste-export'));
  expect(screen.queryByRole('link', { name: 'Definitieve export downloaden' })).toBeNull();
});

const verify: RestoreVerify = {
  upload_id: 'a'.repeat(32),
  filename: 'huis-2026-11-01.shcbak',
  size_bytes: 52_428_800,
  format_version: 2,
  created_at: '2026-11-01T03:00:00Z',
  app_version: '0.1.0',
  schema_revision: '0002_improvements',
  current: { app_version: '0.2.0', schema_revision: '0003_followups' },
  schema_relation: 'older',
  restorable: true,
  refusal: null,
  secrets_included: true,
  redacted: false,
  zigbee2mqtt_included: true,
  zigbee2mqtt: null,
  tables: { homes: 1, devices: 12 },
  total_records: 48_211,
  homes: [{ id: 'h0', name: 'Oude woning', timezone: 'Europe/Amsterdam', active: true }],
  devices: [
    { id: 'd1', home_id: 'h0', provider: 'hue', kind: 'hue_bridge', name: 'Bridge', stable_identity: 'b1', model: 'BSB002', capabilities: [] },
    { id: 'd2', home_id: 'h0', provider: 'gree', kind: 'ac_unit', name: 'Woonkamer', stable_identity: 'mac', model: null, capabilities: [] },
  ],
  integrations: [{ id: 'i1', home_id: 'h0', provider: 'hue', name: 'Hue', has_credentials: true }],
  credentials: { ok: 1, failed: [], total: 1 },
  warnings: ['Deze back-up komt van een oudere versie.'],
};

const job = (state: RestoreJob['state']): RestoreJob => ({
  job_id: 'job1',
  state,
  step: state === 'running' ? 'controleren en terugzetten' : 'klaar',
  mode: 'new_home',
  started_at: '2026-11-08T10:00:00Z',
  finished_at: state === 'done' ? '2026-11-08T10:02:00Z' : null,
  error: null,
  report:
    state === 'done'
      ? { schema_from: '0002_improvements', schema_to: '0003_followups', migrated: true, counts: { homes: 2 }, total_records: 48_211, dropped: { tables: {}, columns: {}, rows_removed_by_migration: {} }, mode: 'new_home', new_home_id: 'h9', archived_homes: ['h0'], zigbee2mqtt_included: true }
      : null,
});

const compare: RestoreCompare = {
  restored_at: '2026-11-08T10:02:00Z',
  safe_mode: true,
  restored_from: { created_at: '2026-11-01T03:00:00Z', app_version: '0.1.0', schema_revision: '0002_improvements', restored_at: '2026-11-08T10:02:00Z', mode: 'new_home' },
  devices: [
    { id: 'd1', home_id: 'h0', name: 'Bridge', provider: 'hue', kind: 'hue_bridge', stable_identity: 'b1', status: 'compatible', reason: 'Gevonden op identiteit en bereikbaar.', online: true, last_seen: '2026-11-08T10:03:00Z', missing: [] },
    { id: 'd2', home_id: 'h0', name: 'Woonkamer', provider: 'gree', kind: 'ac_unit', stable_identity: 'mac', status: 'unavailable', reason: 'Nog niet gezien sinds het terugzetten.', online: false, last_seen: null, missing: [] },
  ],
  functions: [
    { key: 'zone:z1:airco', label: 'Woonkamer (airco)', home_id: 'h0', device_id: 'd2', device_name: 'Woonkamer', status: 'unavailable', reason: 'Nog niet gezien sinds het terugzetten.', suggestion: 'Zet het apparaat aan of koppel het opnieuw.' },
    { key: 'lights:d1', label: 'Licht Keuken', home_id: 'h0', device_id: 'd1', device_name: 'Bridge', status: 'compatible', reason: 'Gevonden op identiteit en bereikbaar.', suggestion: null },
  ],
  summary: { compatible: 1, partial: 0, unavailable: 1 },
};

describe('restore / verhuisassistent (item 43)', () => {
  it('zet gekozen Zigbee-bestanden gezamenlijk terug in een afzonderlijke herstelmap', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr);
    FakeXhr.next = { status: 200, body: { map: 'zigbee-herstel/proefkopie', bestanden: ['configuration.yaml', 'database.db'] } };
    mockApi(() => undefined);
    render(<Providers><RestoreSection safeMode={false} /></Providers>);
    const user = userEvent.setup();
    await user.click(screen.getByText('Losse Zigbee2MQTT-bestanden herstellen'));
    await user.upload(screen.getByLabelText('Zigbee2MQTT-bestanden'), [
      new File(['proef'], 'configuration.yaml'), new File(['regel'], 'database.db'),
    ]);
    await user.click(screen.getByRole('button', { name: 'Herstelkopie maken' }));
    expect(await screen.findByText('zigbee-herstel/proefkopie')).toBeInTheDocument();
    expect(screen.getByText('database.db')).toBeInTheDocument();
    expect(FakeXhr.last!.url).toMatch(/\/system\/restore\/zigbee-files$/);
    expect((FakeXhr.last!.body as FormData).getAll('file').map(f => (f as File).name)).toEqual(['configuration.yaml', 'database.db']);
  });

  it('herstelt een dump met bevestigde herkomst pas na het opgeven van een nieuwe databasenaam', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr);
    FakeXhr.next = { status: 200, body: { kind: 'database_dump', plain_bytes: 1720, dump_upload_id: 'proefdump' } };
    const calls = mockApi((_method, url) => url.endsWith('/restore/dump') ? { body: { database: 'proefkopie' } } : undefined);
    render(<Providers><RestoreSection safeMode={false} /></Providers>);
    const user = userEvent.setup();
    await user.upload(screen.getByLabelText(/Back-upbestand/), new File(['proef'], 'los.shcdump'));
    await user.type(screen.getByLabelText('Wachtwoordzin van de back-up'), 'synthetische proefzin');
    await user.click(screen.getByRole('button', { name: /uploaden en controleren/i }));
    const naam = await screen.findByLabelText('Naam van de nieuwe database');
    const bevestig = screen.getByRole('button', { name: 'In nieuwe database herstellen' });
    expect(bevestig).toBeDisabled();
    await user.type(naam, 'proefkopie');
    await user.click(bevestig);
    expect(await screen.findByText('proefkopie')).toBeInTheDocument();
    expect(calls.filter(c => c.url.endsWith('/restore/dump')).map(c => c.body)).toEqual([
      { upload_id: 'proefdump', passphrase: 'synthetische proefzin', new_database: 'proefkopie' },
    ]);
  });

  it('controleert een losse database-dump en houdt de actieve woning buiten de herstelkeuze', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr);
    const user = userEvent.setup();
    FakeXhr.next = { status: 200, body: { kind: 'database_dump', plain_bytes: 1720 } };
    mockApi(() => undefined);
    render(<Providers><RestoreSection safeMode={false} /></Providers>);
    const invoer = screen.getByLabelText(/Back-upbestand/);
    expect(invoer).toHaveAttribute('accept', expect.stringContaining('.shcdump'));
    await user.upload(invoer, new File(['proef'], 'los.shcdump'));
    await user.type(screen.getByLabelText('Wachtwoordzin van de back-up'), 'synthetische proefzin');
    await user.click(screen.getByRole('button', { name: /controleren/i }));
    expect(await screen.findByText('Database-dump gecontroleerd')).toBeInTheDocument();
    expect(screen.getByText('shc-api restore-dump')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /Controleren en kiezen/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /herstel starten/i })).toBeNull();
  });

  it('upload with progress → verify summary → choose B → apply → poll job → compare → release safe mode', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr);
    FakeXhr.next = { status: 200, body: verify };
    let polls = 0;
    const approval = 'bevestiging-uit-de-proef';
    const rollback = 'doelkopie-uit-de-proef';
    const calls = mockApi((m, url) => {
      if (url.endsWith('/system/restore/preview')) return { body: { confirmation: approval, comparison: {
        mode: 'new_home', rollback_id: rollback, homes_before: [{ name: 'Huidige woning' }], homes_after: verify.homes,
        tables: { homes: { before: 1, after: 1, same: false } },
      } } };
      if (url.endsWith('/system/restore/apply')) return { status: 202, body: { job_id: 'job1', status: 'running', poll: '/api/v1/system/restore/jobs/job1' } };
      if (url.endsWith('/system/restore/jobs/job1')) return { body: job(++polls >= 2 ? 'done' : 'running') };
      if (url.includes('/system/restore/compare')) return { body: compare };
      if (url.endsWith('/system/safe-mode/release') && m === 'POST') return { body: { safe_mode: false } };
      return undefined;
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<Providers><RestoreSection safeMode={false} /></Providers>);
    const user = userEvent.setup();

    // 1: upload + verify
    await user.upload(screen.getByLabelText(/Back-upbestand/), new File(['SHCBAK2\n...'], 'huis-2026-11-01.shcbak'));
    await user.type(screen.getByLabelText('Wachtwoordzin van de back-up'), 'mijn-lange-zin');
    await user.click(screen.getByRole('button', { name: 'Uploaden en controleren' }));
    expect(await screen.findByText('2 · Controleren en kiezen')).toBeInTheDocument();
    const form = FakeXhr.last!.body as FormData;
    expect(form.get('passphrase')).toBe('mijn-lange-zin');
    expect((form.get('file') as File).name).toBe('huis-2026-11-01.shcbak');
    expect(screen.getByText('app 0.1.0 · schema 0002_improvements')).toBeInTheDocument();
    expect(screen.getByText('Oudere versie: wordt na het terugzetten bijgewerkt')).toBeInTheDocument();
    expect(screen.getByText('Oude woning')).toBeInTheDocument();
    expect(screen.getByText(/2 \(Philips Hue 1, Gree airco 1\)/)).toBeInTheDocument();
    expect(screen.getByText('Deze back-up komt van een oudere versie.')).toBeInTheDocument();

    // 2: choose B, confirm, apply
    expect(screen.getByRole('button', { name: 'Herstellen' })).toBeDisabled();
    await user.click(screen.getByRole('radio', { name: /B · Nieuwe woning/ }));
    expect(screen.getByRole('checkbox', { name: /alle gegevens op deze server worden vervangen/ })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Doelback-up maken en vergelijken' }));
    expect(await screen.findByRole('heading', { name: 'Vergelijking vóór terugzetten' })).toBeVisible();
    expect(screen.getByRole('link', { name: /Geverifieerde doelback-up/ })).toHaveAttribute('href', expect.stringContaining(rollback));
    expect(screen.getByText('Verschil')).toBeVisible();
    await user.click(screen.getByRole('checkbox', { name: /alle gegevens op deze server worden vervangen/ }));
    await user.click(screen.getByRole('button', { name: 'Herstellen' }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/restore/apply'))?.body).toEqual({ upload_id: verify.upload_id, passphrase: 'mijn-lange-zin', mode: 'new_home', rollback_id: rollback, confirmation: approval }));
    expect(JSON.parse(localStorage.getItem(RESTORE_KEY)!)).toMatchObject({ job_id: 'job1', mode: 'new_home' });

    // 3: running, then done
    expect(await screen.findByText(/Bezig: controleren en terugzetten/)).toBeInTheDocument();
    expect(await screen.findByText(/Herstel klaar/, undefined, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText('0002_improvements → 0003_followups (bijgewerkt)')).toBeInTheDocument();

    // 4: compare per function/device, then release
    expect(await screen.findByText('4 · Vergelijken: wat werkt er?')).toBeInTheDocument();
    const row = screen.getByText('Woonkamer (airco)').closest('tr')!;
    expect(within(row).getByText('Niet beschikbaar')).toBeInTheDocument();
    expect(within(row).getByText('Zet het apparaat aan of koppel het opnieuw.')).toBeInTheDocument();
    expect(within(screen.getByText('Licht Keuken').closest('tr')!).getByText('Werkt')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Veilige modus opheffen' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/system/safe-mode/release'))).toBe(true));
    expect(localStorage.getItem(RESTORE_KEY)).toBeNull();
  }, 15000);

  it('names what was dropped: the columns per table and the records the update removed (item 34)', async () => {
    localStorage.setItem(RESTORE_KEY, JSON.stringify({ job_id: 'job1', mode: 'same_home', started_at: Date.now() }));
    const done = job('done');
    done.report = {
      ...done.report!,
      mode: 'same_home',
      dropped: { tables: { old_table: 2 }, columns: { homes: ['legacy_flag', 'old_note'], zones: ['x'] }, rows_removed_by_migration: { tariffs: 1, readings: 1250 } },
    };
    mockApi((_m, url) => (url.endsWith('/system/restore/jobs/job1') ? { body: done } : undefined));
    render(<Providers><RestoreSection safeMode={false} /></Providers>);
    expect(await screen.findByText(/Herstel klaar/)).toBeInTheDocument();
    const items = screen.getAllByRole('listitem').map((li) => li.textContent);
    expect(items).toEqual([
      'tabel old_table: 2 records (bestaat niet in deze versie)',
      'homes: kolommen legacy_flag, old_note (bestaan niet in deze versie)',
      'zones: kolom x (bestaat niet in deze versie)',
      'tariffs: 1 record opgeruimd door de database-update',
      'readings: 1.250 records opgeruimd door de database-update',
    ]);
    expect(screen.getByText('Niet (volledig) overgenomen')).toBeInTheDocument();
  });

  it('shows no dropped list when nothing was dropped', () => {
    expect(droppedLines({ tables: {}, columns: {}, rows_removed_by_migration: {} })).toEqual([]);
    expect(droppedLines(undefined)).toEqual([]);
  });

  it('a wrong passphrase shows the Dutch refusal and stays on step 1', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr);
    FakeXhr.next = { status: 422, body: { detail: { code: 'backup_invalid', message: 'Wachtwoordzin klopt niet of het bestand is beschadigd.' } } };
    mockApi(() => undefined);
    render(<Providers><RestoreSection safeMode={false} /></Providers>);
    const user = userEvent.setup();
    await user.upload(screen.getByLabelText(/Back-upbestand/), new File(['x'], 'b.shcbak'));
    await user.type(screen.getByLabelText('Wachtwoordzin van de back-up'), 'fout');
    await user.click(screen.getByRole('button', { name: 'Uploaden en controleren' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Wachtwoordzin klopt niet');
    expect(screen.queryByText('2 · Controleren en kiezen')).not.toBeInTheDocument();
  });

  it('a refused (newer) backup cannot be applied', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr);
    FakeXhr.next = { status: 200, body: { ...verify, schema_relation: 'newer', restorable: false, refusal: 'Deze back-up komt van een nieuwere versie.' } };
    mockApi(() => undefined);
    render(<Providers><RestoreSection safeMode={false} /></Providers>);
    const user = userEvent.setup();
    await user.upload(screen.getByLabelText(/Back-upbestand/), new File(['x'], 'b.shcbak'));
    await user.type(screen.getByLabelText('Wachtwoordzin van de back-up'), 'zin');
    await user.click(screen.getByRole('button', { name: 'Uploaden en controleren' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('kan hier niet worden teruggezet');
    expect(screen.queryByRole('button', { name: 'Herstellen' })).not.toBeInTheDocument();
  });

  it('the login page follows a running restore (no login needed for the job)', async () => {
    localStorage.setItem(RESTORE_KEY, JSON.stringify({ job_id: 'job1', mode: 'same_home', started_at: Date.now() }));
    mockApi((_m, url) => (url.endsWith('/system/restore/jobs/job1') ? { body: { ...job('done'), mode: 'same_home' } } : undefined));
    render(<Providers user={null}><PendingRestoreNotice /></Providers>);
    expect(await screen.findByText(/Klaar\. Log in/)).toBeInTheDocument();
  });
});

it('toont een gewijzigde knopactie bij gelijke aantallen en houdt de vrijgave dicht', () => {
  const data: RestoreCompare = {
    ...compare, migration: { source_id: 'bron', target_id: 'doel', confirmation: null, source_complete: true },
    inventory: { button_mappings: { source: 1, target: 1, same: false }, sensor_readings: { source: 2, target: 2, same: true } },
  };
  render(<Providers><CompareView data={data} /></Providers>);
  const row = screen.getByRole('row', { name: /Knoppen, gebeurtenissen en acties/ });
  expect(within(row).getAllByText('1')).toHaveLength(2);
  expect(within(row).getByText('Wijkt af')).toBeVisible();
  expect(screen.getByText('Overstap geblokkeerd.', { exact: false })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Overstap bevestigen' })).toBeNull();
});

it('toont een ontbrekende rij naast overeenkomend inbedrijfstellingsbeleid', () => {
  const data: RestoreCompare = {
    ...compare, inventory: { commissioning_gate: { source: 1, target: 0, same: false } },
    commissioning_gate: { source: 1, target: 1, same: true },
  };
  render(<Providers><CompareView data={data} /></Providers>);
  const row = screen.getByRole('row', { name: /commissioning_gate/ });
  expect(within(row).getByText('0')).toBeVisible();
  expect(within(row).getByText('Wijkt af')).toBeVisible();
  expect(screen.getByText('Het inbedrijfstellingsbeleid komt overeen.')).toBeVisible();
});

it('toont waarom een overstap met een ouder bronbewijs wordt geweigerd', () => {
  const refusal = 'Maak een nieuwe definitieve export op de bijgewerkte bron.';
  const data = { ...compare, migration: { source_id: 'bron', target_id: 'doel', confirmation: null,
    source_complete: false, refusal } };
  render(<Providers><CompareView data={data} /></Providers>);
  expect(screen.getByText(refusal, { exact: false })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Overstap bevestigen' })).toBeNull();
});
