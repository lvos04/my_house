import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { RestoreCompare, RestoreJob, RestoreVerify } from '../api/types';
import { RestoreSection } from '../pages/system/Restore';
import { PendingRestoreNotice, RESTORE_KEY } from '../pages/system/restoreState';
import { FakeXhr, mockApi, Providers } from './helpers';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
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
  it('upload with progress → verify summary → choose B → apply → poll job → compare → release safe mode', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr);
    FakeXhr.next = { status: 200, body: verify };
    let polls = 0;
    const calls = mockApi((m, url) => {
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
    await user.upload(screen.getByLabelText('Back-upbestand (.shcbak)'), new File(['SHCBAK2\n...'], 'huis-2026-11-01.shcbak'));
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
    await user.click(screen.getByRole('checkbox', { name: /alle gegevens op deze server worden vervangen/ }));
    await user.click(screen.getByRole('button', { name: 'Herstellen' }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/restore/apply'))?.body).toEqual({ upload_id: verify.upload_id, passphrase: 'mijn-lange-zin', mode: 'new_home' }));
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

  it('a wrong passphrase shows the Dutch refusal and stays on step 1', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr);
    FakeXhr.next = { status: 422, body: { detail: { code: 'backup_invalid', message: 'Wachtwoordzin klopt niet of het bestand is beschadigd.' } } };
    mockApi(() => undefined);
    render(<Providers><RestoreSection safeMode={false} /></Providers>);
    const user = userEvent.setup();
    await user.upload(screen.getByLabelText('Back-upbestand (.shcbak)'), new File(['x'], 'b.shcbak'));
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
    await user.upload(screen.getByLabelText('Back-upbestand (.shcbak)'), new File(['x'], 'b.shcbak'));
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
