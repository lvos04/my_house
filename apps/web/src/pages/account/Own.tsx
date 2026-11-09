import { useState, type FormEvent } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { qk } from '../../api/keys';
import { ActionButton, ErrorNote } from '../../components/bits';
import { useLogout } from '../../components/useLogout';
import { dateTime } from '../../format';
import { CLIENT_ROLE_LABEL, USER_ROLE_LABEL, t } from '../../i18n';
import { useCan, useUser } from '../../user';

/** Change your own password; optionally also sign out your paired phones (verbeterlijst 18). */
export function PasswordForm() {
  const qc = useQueryClient();
  const [oldPw, setOldPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [repeat, setRepeat] = useState('');
  const [signOutPhones, setSignOutPhones] = useState(true);
  const [localError, setLocalError] = useState<string | null>(null);
  const change = useMutation({
    mutationFn: () => api.changePassword({ old_password: oldPw, new_password: newPw, keep_phones: !signOutPhones }),
    onSuccess: () => {
      setOldPw('');
      setNewPw('');
      setRepeat('');
      qc.invalidateQueries({ queryKey: qk.me });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setLocalError(null);
    if (newPw.length < 12) return setLocalError('Kies een nieuw wachtwoord van minstens 12 tekens.');
    if (newPw !== repeat) return setLocalError('De nieuwe wachtwoorden komen niet overeen.');
    if (newPw === oldPw) return setLocalError('Kies een ander wachtwoord dan je huidige.');
    change.mutate();
  };
  return (
    <form className="stack" onSubmit={submit} aria-labelledby="pw-h">
      <h3 id="pw-h">Wachtwoord wijzigen</h3>
      <div className="form-grid">
        <label className="field">Huidig wachtwoord<input type="password" autoComplete="current-password" required value={oldPw} onChange={(e) => setOldPw(e.target.value)} /></label>
        <label className="field">Nieuw wachtwoord (min. 12 tekens)<input type="password" autoComplete="new-password" required value={newPw} onChange={(e) => setNewPw(e.target.value)} /></label>
        <label className="field">Herhaal nieuw wachtwoord<input type="password" autoComplete="new-password" required value={repeat} onChange={(e) => setRepeat(e.target.value)} /></label>
      </div>
      <label className="check-row">
        <input type="checkbox" checked={signOutPhones} onChange={(e) => setSignOutPhones(e.target.checked)} />
        Ook mijn gekoppelde telefoons afmelden (ze moeten daarna opnieuw koppelen)
      </label>
      <p className="small muted" style={{ margin: 0 }}>Je andere browsersessies worden altijd afgemeld. Deze sessie blijft ingelogd.</p>
      <div><ActionButton type="submit" className="btn primary" disabled={change.isPending}>{change.isPending ? 'Bezig…' : 'Wachtwoord wijzigen'}</ActionButton></div>
      {localError && <div className="note error" role="alert">{localError}</div>}
      {change.error && <ErrorNote error={change.error} />}
      {change.data && (
        <div className="note ok" role="status">
          Wachtwoord gewijzigd. {change.data.sessions_ended} andere sessie(s) afgemeld, {change.data.phones_revoked} telefoon(s) afgemeld.
        </div>
      )}
    </form>
  );
}

export function OwnPanel() {
  const user = useUser();
  const can = useCan();
  const logout = useLogout();
  if (!user) return null;
  return (
    <div className="stack">
      <dl className="facts">
        <div><dt>Gebruikersnaam</dt><dd>{user.username}</dd></div>
        <div><dt>Rol</dt><dd>{t(USER_ROLE_LABEL, user.role)}{user.client_role ? ` · telefoon: ${t(CLIENT_ROLE_LABEL, user.client_role)}` : ''}</dd></div>
        <div><dt>Mag bedienen</dt><dd>{can.control ? 'ja' : 'nee (alleen kijken)'}</dd></div>
        <div><dt>Wachtwoord gewijzigd</dt><dd>{dateTime(user.password_changed_at ?? null)}</dd></div>
      </dl>
      <PasswordForm />
      <hr />
      <div className="row">
        <button type="button" className="btn" disabled={logout.isPending} onClick={() => logout.mutate()}>Uitloggen</button>
      </div>
    </div>
  );
}
