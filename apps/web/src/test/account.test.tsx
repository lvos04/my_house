import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AuditEntry, UserFull } from '../api/types';
import { AUDIT_ACTION_LABEL } from '../i18n';
import { AccountPage } from '../pages/Account';
import { UsersPanel } from '../pages/account/Users';
import { PairPhone, PhoneList } from '../pages/account/Phones';
import { AuditPanel, AUDIT_PAGE } from '../pages/account/Audit';
import { PasswordForm } from '../pages/account/Own';
import { admin, mockApi, Providers, viewer } from './helpers';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

const full = (over: Partial<UserFull>): UserFull => ({
  id: 'u1', username: 'admin', role: 'admin', disabled: false, created_at: '2026-10-01T10:00:00Z', password_changed_at: null,
  failed_logins: 0, locked_until: null, locked: false, active_sessions: 1, active_phones: 0, ...over,
});

describe('user management (item 18)', () => {
  it('lists users, changes a role, disables, unlocks, deletes and adds; never offers actions on yourself', async () => {
    let users: UserFull[] = [
      full({}),
      full({ id: 'u2', username: 'anna', role: 'viewer', locked: true, locked_until: '2026-11-08T12:00:00Z', failed_logins: 5 }),
    ];
    const calls = mockApi((m, url, body) => {
      if (url.endsWith('/users') && m === 'GET') return { body: users };
      if (url.endsWith('/users') && m === 'POST') {
        const u = full({ id: 'u3', username: (body as { username: string }).username, role: (body as { role: 'viewer' }).role });
        users = [...users, u];
        return { body: u };
      }
      if (url.endsWith('/users/u2') && m === 'PATCH') {
        users = users.map((u) => (u.id === 'u2' ? { ...u, ...(body as object), locked: (body as { unlock?: boolean }).unlock ? false : u.locked } : u));
        return { body: users.find((u) => u.id === 'u2') };
      }
      if (url.endsWith('/users/u2') && m === 'DELETE') {
        users = users.filter((u) => u.id !== 'u2');
        return { status: 204 };
      }
      return undefined;
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<Providers><UsersPanel /></Providers>);
    const user = userEvent.setup();
    const annaRow = (await screen.findByText('anna')).closest('tr')!;
    const adminRow = screen.getByText('admin').closest('tr')!;
    expect(within(adminRow).getByText('(jij)')).toBeInTheDocument();
    expect(within(adminRow).queryByRole('button')).not.toBeInTheDocument();
    expect(within(annaRow).getByText(/Geblokkeerd tot/)).toBeInTheDocument();

    await user.selectOptions(within(annaRow).getByLabelText('Rol van anna'), 'admin');
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ role: 'admin' }));
    await user.click(within(screen.getByText('anna').closest('tr')!).getByRole('button', { name: 'Ontgrendelen' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'PATCH').map((c) => c.body)).toContainEqual({ unlock: true }));
    await user.click(within(screen.getByText('anna').closest('tr')!).getByRole('button', { name: 'Uitschakelen' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'PATCH').map((c) => c.body)).toContainEqual({ disabled: true }));
    expect(await within(screen.getByText('anna').closest('tr')!).findByRole('button', { name: 'Inschakelen' })).toBeInTheDocument();

    await user.type(screen.getByLabelText('Gebruikersnaam'), 'bob');
    await user.type(screen.getByLabelText('Wachtwoord (min. 12 tekens)'), 'een-lang-wachtwoord');
    await user.click(screen.getByRole('button', { name: 'Toevoegen' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ username: 'bob', password: 'een-lang-wachtwoord', role: 'viewer' }));
    expect(await screen.findByText('bob')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'anna verwijderen' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/users/u2'))).toBe(true));
    await waitFor(() => expect(screen.queryByText('anna')).not.toBeInTheDocument());
  });

  it('shows the server refusal (last admin) in Dutch', async () => {
    mockApi((m, url) => {
      if (url.endsWith('/users') && m === 'GET') return { body: [full({}), full({ id: 'u2', username: 'anna' })] };
      if (m === 'PATCH') return { status: 409, body: { detail: { code: 'last_admin', message: 'Er moet minstens één actieve beheerder blijven.' } } };
      return undefined;
    });
    render(<Providers><UsersPanel /></Providers>);
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText('Rol van anna'), 'viewer');
    expect(await screen.findByRole('alert')).toHaveTextContent('Er moet minstens één actieve beheerder blijven.');
  });

  it('a short password is refused before sending', async () => {
    const calls = mockApi((_m, url) => (url.endsWith('/users') ? { body: [full({})] } : undefined));
    render(<Providers><UsersPanel /></Providers>);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText('Gebruikersnaam'), 'bob');
    await user.type(screen.getByLabelText('Wachtwoord (min. 12 tekens)'), 'kort');
    await user.click(screen.getByRole('button', { name: 'Toevoegen' }));
    expect(screen.getByRole('alert')).toHaveTextContent('minstens 12 tekens');
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });
});

describe('own password', () => {
  it('"ook telefoons afmelden" maps to keep_phones=false, unticked to true', async () => {
    const calls = mockApi((m) => (m === 'POST' ? { body: { sessions_ended: 2, phones_revoked: 1 } } : undefined));
    render(<Providers><PasswordForm /></Providers>);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Huidig wachtwoord'), 'oud-wachtwoord-1');
    await user.type(screen.getByLabelText('Nieuw wachtwoord (min. 12 tekens)'), 'nieuw-wachtwoord-2');
    await user.type(screen.getByLabelText('Herhaal nieuw wachtwoord'), 'nieuw-wachtwoord-2');
    expect(screen.getByLabelText(/Ook mijn gekoppelde telefoons afmelden/)).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Wachtwoord wijzigen' }));
    expect(await screen.findByRole('status')).toHaveTextContent('2 andere sessie(s) afgemeld, 1 telefoon(s) afgemeld');
    expect(calls[0]).toMatchObject({ url: '/api/v1/auth/password', body: { old_password: 'oud-wachtwoord-1', new_password: 'nieuw-wachtwoord-2', keep_phones: false } });
  });
});

describe('phones', () => {
  it('pairing defaults to "Bedienen"; admin needs the password', async () => {
    const qr = JSON.stringify({ v: 1, type: 'shc-pair', code: 'K7Q4-9XPM', lan: 'http://192.168.1.20:8088', remote: null, home: 'Thuis', server_id: 's', role: 'admin' });
    const calls = mockApi(() => ({ body: { code: 'K7Q4-9XPM', expires_at: '2026-11-08T14:50:00Z', role: 'admin', server_id: 's', qr_payload: qr } }));
    render(<Providers><PairPhone /></Providers>);
    expect(screen.getByRole('radio', { name: /Bedienen/ })).toBeChecked();
    const user = userEvent.setup();
    await user.click(screen.getByRole('radio', { name: /Beheren/ }));
    // An admin phone never manages users, phones or backups (the server answers 403 web_session_required).
    expect(screen.getByRole('radio', { name: /Beheren/ }).closest('label')).toHaveTextContent(
      /Gebruikers, telefoons, back-ups, adressen en pushmeldingen alleen in de web-app\. Vraagt je wachtwoord\./,
    );
    expect(screen.queryByText(/Alles, ook gebruikers/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'QR-code maken' })).toBeDisabled();
    await user.type(screen.getByLabelText(/Je eigen wachtwoord/), 'mijn-wachtwoord');
    await user.click(screen.getByRole('button', { name: 'QR-code maken' }));
    expect(await screen.findByTestId('pair-code')).toHaveTextContent('K7Q4-9XPM');
    expect(calls[0].body).toEqual({ role: 'admin', password: 'mijn-wachtwoord' });
  });

  it('links to Systeem › Adressen when the home address is missing', async () => {
    mockApi(() => ({ status: 409, body: { detail: { code: 'lan_url_required', message: 'Stel eerst het thuisadres in via Systeem › Adressen.' } } }));
    render(<Providers><PairPhone /></Providers>);
    await userEvent.setup().click(screen.getByRole('button', { name: 'QR-code maken' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Stel eerst het thuisadres in');
    expect(screen.getAllByRole('link', { name: /Adressen/ })[0]).toHaveAttribute('href', '/systeem#adressen');
  });

  it('lists phones with role, expiry and state, and revokes', async () => {
    const calls = mockApi((m, url) => {
      if (url.endsWith('/clients') && m === 'GET')
        return {
          body: [
            { id: 'c1', name: 'Pixel', role: 'control', created_at: '2026-10-01T10:00:00Z', last_used_at: null, expires_at: '2027-03-30T10:00:00Z', refreshed_at: null, revoked: false, expired: false, user: 'admin' },
            { id: 'c2', name: 'Oud', role: 'viewer', created_at: '2026-01-01T10:00:00Z', last_used_at: null, expires_at: '2026-06-30T10:00:00Z', refreshed_at: null, revoked: false, expired: true, user: 'anna' },
          ],
        };
      if (m === 'DELETE') return { status: 204 };
      return undefined;
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<Providers><PhoneList /></Providers>);
    const pixel = (await screen.findByText('Pixel')).closest('tr')!;
    expect(within(pixel).getByText('Bedienen')).toBeInTheDocument();
    expect(within(pixel).getByText('actief')).toBeInTheDocument();
    expect(within(screen.getByText('Oud').closest('tr')!).getByText('verlopen')).toBeInTheDocument();
    await userEvent.setup().click(within(pixel).getByRole('button', { name: 'Toegang van Pixel intrekken' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/clients/c1'))).toBe(true));
  });
});

describe('audit log', () => {
  it('pages with before=<last id> and shows Dutch action names', async () => {
    const row = (id: number): AuditEntry => ({ id, at: '2026-11-08T10:00:00Z', actor: 'admin', action: id % 2 ? 'login' : 'user_created', target: `t${id}`, detail: null });
    const calls = mockApi((_m, url) => {
      if (!url.includes('/audit')) return undefined;
      const before = new URL(url, 'http://x').searchParams.get('before');
      if (!before) return { body: Array.from({ length: AUDIT_PAGE }, (_, i) => row(200 - i)) };
      return { body: [row(Number(before) - 1)] };
    });
    render(<Providers><AuditPanel /></Providers>);
    const first = (await screen.findByText('t200')).closest('tr')!;
    expect(within(first).getByText('Gebruiker aangemaakt')).toBeInTheDocument();
    expect(within(screen.getByText('t199').closest('tr')!).getByText('Ingelogd')).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Meer laden' }));
    expect(await screen.findByText('t150')).toBeInTheDocument();
    expect(calls.map((c) => c.url)).toContain(`/api/v1/audit?limit=${AUDIT_PAGE}&before=151`);
    expect(screen.getByText('Einde van het logboek.')).toBeInTheDocument();
  });

  it('names newer actions in Dutch and can filter on them (item 18)', async () => {
    const rows: AuditEntry[] = [
      { id: 3, at: '2026-11-08T10:02:00Z', actor: 'admin', action: 'zigbee_permit_join', target: 'Zigbee-coördinator', detail: { seconds: 120 } },
      { id: 2, at: '2026-11-08T10:01:00Z', actor: 'admin', action: 'notify_url_updated', target: 'https://ntfy.sh/…', detail: { cleared: false } },
      { id: 1, at: '2026-11-08T10:00:00Z', actor: 'experiment', action: 'profile_restored', target: 'z1', detail: { name: 'Winter' } },
    ];
    const calls = mockApi((_m, url) => {
      if (!url.includes('/audit')) return undefined;
      const action = new URL(url, 'http://x').searchParams.get('action');
      return { body: action ? rows.filter((r) => r.action === action) : rows };
    });
    render(<Providers><AuditPanel /></Providers>);
    expect(await screen.findByText('Zigbee: nieuwe apparaten toegelaten')).toBeInTheDocument();
    expect(screen.getByText('Push-adres gewijzigd')).toBeInTheDocument();
    expect(screen.getByText('Profiel en schema teruggezet (na experiment)')).toBeInTheDocument();
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText('Soort'), 'Lichtcommando');
    await user.click(screen.getByRole('button', { name: 'Filteren' }));
    await waitFor(() => expect(calls.map((c) => c.url)).toContain(`/api/v1/audit?limit=${AUDIT_PAGE}&action=light_command`));
  });

  // Every action the API writes with audit(db, actor, "<action>", ...) needs a Dutch label, otherwise the
  // Logboek shows the raw code and the "Soort" filter cannot select it. Skipped when the API source is
  // not next to this app (e.g. a build with only apps/web).
  const apiSrc = join(__dirname, '../../../api/src/shc_api');
  it.skipIf(!existsSync(apiSrc))('has a label for every audit action the API writes', () => {
    const files = (dir: string): string[] =>
      readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
        e.isDirectory() ? (e.name === '__pycache__' ? [] : files(join(dir, e.name))) : e.name.endsWith('.py') ? [join(dir, e.name)] : [],
      );
    const actions = new Set<string>(['request']);
    for (const f of files(apiSrc)) {
      for (const m of readFileSync(f, 'utf8').matchAll(/\baudit\(\s*db\s*,\s*[^,()]+,\s*"([a-z_]+)"/g)) actions.add(m[1]);
    }
    expect(actions.size).toBeGreaterThan(40); // the pattern still finds the calls
    expect([...actions].filter((a) => !(a in AUDIT_ACTION_LABEL)).sort()).toEqual([]);
  });
});

describe('account page tabs', () => {
  it('admins get accessible tabs with arrow-key navigation; viewers only their own account', async () => {
    mockApi(() => ({ body: [] }));
    const { unmount } = render(<Providers><AccountPage /></Providers>);
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual(['Mijn account', 'Gebruikers', 'Telefoons', 'Logboek']);
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', tabs[0].id);
    const user = userEvent.setup();
    tabs[0].focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Gebruikers' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Gebruikers' })).toHaveFocus();
    expect(screen.getByRole('tab', { name: 'Mijn account' })).toHaveAttribute('tabindex', '-1');
    await user.keyboard('{End}');
    expect(screen.getByRole('tab', { name: 'Logboek' })).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Mijn account' })).toHaveAttribute('aria-selected', 'true');
    unmount();
    render(<Providers user={viewer}><AccountPage /></Providers>);
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Wachtwoord wijzigen' })).toBeInTheDocument();
    expect(admin.role).toBe('admin');
  });
});
