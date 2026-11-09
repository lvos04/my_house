import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { qk } from '../../api/keys';
import type { Role, UserFull, UserPatch } from '../../api/types';
import { ActionButton, Empty, ErrorNote, Loading } from '../../components/bits';
import { dateTime, time } from '../../format';
import { USER_ROLE_LABEL } from '../../i18n';
import { useUser } from '../../user';

function AddUser() {
  const qc = useQueryClient();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<Role>('viewer');
  const [localError, setLocalError] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api.createUser({ username: username.trim(), password, role }),
    onSuccess: () => {
      setUsername('');
      setPassword('');
      setRole('viewer');
      qc.invalidateQueries({ queryKey: qk.users });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setLocalError(null);
    if (!/^[A-Za-z0-9_.\-@]{2,64}$/.test(username.trim())) return setLocalError('Gebruikersnaam: 2–64 tekens, alleen letters, cijfers en _ . - @');
    if (password.length < 12) return setLocalError('Het wachtwoord moet minstens 12 tekens zijn.');
    create.mutate();
  };
  return (
    <form className="stack" onSubmit={submit} aria-labelledby="adduser-h">
      <h2 id="adduser-h" className="text-lg font-semibold">Gebruiker toevoegen</h2>
      <div className="form-grid">
        <label className="field">Gebruikersnaam<input autoComplete="off" required value={username} onChange={(e) => setUsername(e.target.value)} /></label>
        <label className="field">Wachtwoord (min. 12 tekens)<input type="password" autoComplete="new-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <label className="field">Rol
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="viewer">{USER_ROLE_LABEL.viewer} (alleen kijken)</option>
            <option value="admin">{USER_ROLE_LABEL.admin} (alles)</option>
          </select>
        </label>
        <ActionButton type="submit" className="btn primary" disabled={create.isPending}>Toevoegen</ActionButton>
      </div>
      {localError && <div className="note error" role="alert">{localError}</div>}
      {create.error && <ErrorNote error={create.error} />}
      {create.isSuccess && <span className="small muted" role="status">Gebruiker toegevoegd.</span>}
    </form>
  );
}

function ResetPassword({ user, onDone }: { user: UserFull; onDone: () => void }) {
  const [password, setPassword] = useState('');
  const reset = useMutation({ mutationFn: () => api.resetUserPassword(user.id, password), onSuccess: () => setPassword('') });
  return (
    <form className="row" onSubmit={(e) => { e.preventDefault(); if (password.length >= 12) reset.mutate(); }} aria-label={`Nieuw wachtwoord voor ${user.username}`}>
      <label className="field" style={{ flex: 1 }}>
        Nieuw wachtwoord voor {user.username}
        <input type="password" autoComplete="new-password" minLength={12} required value={password} onChange={(e) => setPassword(e.target.value)} />
      </label>
      <ActionButton type="submit" className="btn primary" disabled={reset.isPending || password.length < 12}>Instellen</ActionButton>
      <button type="button" className="btn" onClick={onDone}>Sluiten</button>
      {reset.error && <ErrorNote error={reset.error} />}
      {reset.data && (
        <span className="small muted" role="status">
          Ingesteld. {reset.data.sessions_ended} sessie(s) beëindigd, {reset.data.phones_revoked} telefoon(s) afgemeld.
        </span>
      )}
    </form>
  );
}

function statusText(u: UserFull): string {
  if (u.disabled) return 'Uitgeschakeld';
  if (u.locked) return `Geblokkeerd tot ${time(u.locked_until)} (${u.failed_logins} mislukte pogingen)`;
  return 'Actief';
}

/** User management (admin): list, add with role, change role, disable, unlock, reset password, delete. */
export function UsersPanel() {
  const qc = useQueryClient();
  const me = useUser();
  const users = useQuery({ queryKey: qk.users, queryFn: api.users });
  const [resetFor, setResetFor] = useState<string | null>(null);
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: UserPatch }) => api.patchUser(id, body),
    onSuccess: (u) => qc.setQueryData<UserFull[]>(qk.users, (l) => l?.map((x) => (x.id === u.id ? u : x))),
  });
  const remove = useMutation({ mutationFn: api.deleteUser, onSuccess: () => qc.invalidateQueries({ queryKey: qk.users }) });
  const list = Array.isArray(users.data) ? users.data : [];
  return (
    <div className="stack">
      <h2 className="text-lg font-semibold">Gebruikers</h2>
      <p className="small muted" style={{ margin: 0 }}>
        Een kijker ziet alles maar kan niets bedienen of instellen. Uitschakelen of een nieuw wachtwoord instellen meldt die gebruiker overal af, ook op gekoppelde telefoons.
      </p>
      {users.isLoading && <Loading />}
      {users.error && <ErrorNote error={users.error} />}
      {users.data && list.length === 0 && <Empty>Geen gebruikers.</Empty>}
      {list.length > 0 && (
        <div className="table-wrap">
          <table className="stack-mobile">
            <thead><tr><th scope="col">Gebruiker</th><th scope="col">Rol</th><th scope="col">Status</th><th scope="col">Actief op</th><th scope="col"><span className="sr-only">Acties</span></th></tr></thead>
            <tbody>
              {list.map((u) => {
                const self = u.id === me?.id;
                return (
                  <tr key={u.id}>
                    <td data-label="Gebruiker">
                      <strong>{u.username}</strong>{self && <span className="small muted"> (jij)</span>}
                      <div className="small muted">sinds {dateTime(u.created_at)}</div>
                    </td>
                    <td data-label="Rol">
                      {self ? (
                        USER_ROLE_LABEL[u.role]
                      ) : (
                        <select aria-label={`Rol van ${u.username}`} value={u.role} disabled={patch.isPending} onChange={(e) => patch.mutate({ id: u.id, body: { role: e.target.value as Role } })}>
                          <option value="admin">{USER_ROLE_LABEL.admin}</option>
                          <option value="viewer">{USER_ROLE_LABEL.viewer}</option>
                        </select>
                      )}
                    </td>
                    <td data-label="Status">{statusText(u)}</td>
                    <td data-label="Actief op" className="small">{u.active_sessions} browser(s) · {u.active_phones} telefoon(s)</td>
                    <td className="actions">
                      {!self && (
                        <div className="row" style={{ gap: 6 }}>
                          {u.locked && (
                            <ActionButton className="btn small" onClick={() => patch.mutate({ id: u.id, body: { unlock: true } })}>Ontgrendelen</ActionButton>
                          )}
                          <ActionButton
                            className="btn small"
                            onClick={() => {
                              if (u.disabled || window.confirm(`${u.username} uitschakelen? Alle sessies en telefoons van deze gebruiker worden afgemeld.`))
                                patch.mutate({ id: u.id, body: { disabled: !u.disabled } });
                            }}
                          >
                            {u.disabled ? 'Inschakelen' : 'Uitschakelen'}
                          </ActionButton>
                          <ActionButton className="btn small" onClick={() => setResetFor(resetFor === u.id ? null : u.id)} aria-expanded={resetFor === u.id}>Nieuw wachtwoord</ActionButton>
                          <ActionButton
                            className="btn danger small"
                            aria-label={`${u.username} verwijderen`}
                            onClick={() => { if (window.confirm(`${u.username} definitief verwijderen? Sessies, telefoons en koppelcodes van deze gebruiker verdwijnen ook.`)) remove.mutate(u.id); }}
                          >
                            Verwijderen
                          </ActionButton>
                        </div>
                      )}
                      {resetFor === u.id && <ResetPassword user={u} onDone={() => setResetFor(null)} />}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {(patch.error || remove.error) && <ErrorNote error={patch.error ?? remove.error} />}
      <hr />
      <AddUser />
    </div>
  );
}
