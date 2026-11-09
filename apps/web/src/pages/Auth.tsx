import { useState, type FormEvent } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import { HomeIcon } from '../components/Icons';
import { OfflineBanner } from '../components/OfflineBanner';
import { useConnection } from '../state';
import { SIGNOUT_REASON_LABEL } from '../i18n';
import { PendingRestoreNotice } from './system/restoreState';

/** Why this login page is shown again (WS close 4401/4403 or a 401 on a request). */
export function signoutText(code: string | undefined, reason: string | undefined): string | null {
  if (!code) return null;
  if (reason && SIGNOUT_REASON_LABEL[reason]) return SIGNOUT_REASON_LABEL[reason];
  if (code === 'token_revoked') return 'De toegang van dit apparaat is ingetrokken.';
  if (code === 'token_expired') return 'De toegang van dit apparaat is verlopen.';
  return 'Je sessie is beëindigd. Log opnieuw in.';
}

export function AuthPage({ mode }: { mode: 'setup' | 'login' }) {
  const qc = useQueryClient();
  const { authNotice, setAuthNotice } = useConnection();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);

  const m = useMutation({
    mutationFn: () => (mode === 'setup' ? api.setup(username, password) : api.login(username, password)),
    onSuccess: () => {
      setAuthNotice(null);
      qc.invalidateQueries(); // data of a previous session/user must not be reused
      qc.invalidateQueries({ queryKey: qk.auth });
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setLocalError(null);
    if (mode === 'setup') {
      if (password.length < 12) return setLocalError('Kies een wachtwoord van minstens 12 tekens.');
      if (password !== repeat) return setLocalError('De wachtwoorden komen niet overeen.');
    }
    m.mutate();
  };
  const notice = mode === 'login' ? signoutText(authNotice?.code, authNotice?.reason) : null;

  return (
    <div className="auth">
      <form className="card" onSubmit={submit} aria-labelledby="auth-h">
        <div className="brand" style={{ padding: 0 }}>
          <span className="brand-mark"><HomeIcon width={18} height={18} strokeWidth={2} /></span>
          Huis
        </div>
        <OfflineBanner />
        <PendingRestoreNotice />
        <div>
          <h1 id="auth-h" style={{ fontSize: 26 }}>{mode === 'setup' ? 'Beheerder aanmaken' : 'Inloggen'}</h1>
          <p className="muted small" style={{ margin: '6px 0 0' }}>
            {mode === 'setup'
              ? 'Eerste start: maak het beheerdersaccount aan. Er is geen standaardwachtwoord. Daarna maak je je woning aan.'
              : 'Log in met je account voor deze installatie.'}
          </p>
        </div>
        {notice && <div className="note info" role="status">{notice}</div>}
        <label className="field">
          Gebruikersnaam
          <input autoComplete="username" required value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="field">
          Wachtwoord
          <input type="password" autoComplete={mode === 'setup' ? 'new-password' : 'current-password'} required minLength={mode === 'setup' ? 12 : undefined} value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {mode === 'setup' && (
          <label className="field">
            Herhaal wachtwoord
            <input type="password" autoComplete="new-password" required value={repeat} onChange={(e) => setRepeat(e.target.value)} />
          </label>
        )}
        {(localError || m.error) && <p className="error-text" role="alert">{localError ?? (m.error as Error).message}</p>}
        <button className="btn primary" type="submit" disabled={m.isPending}>
          {m.isPending ? 'Bezig…' : mode === 'setup' ? 'Account aanmaken' : 'Inloggen'}
        </button>
      </form>
    </div>
  );
}
