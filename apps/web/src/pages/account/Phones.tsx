import { useEffect, useState, type FormEvent } from 'react';
import QRCode from 'qrcode';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, isApiError, parseQrPayload } from '../../api/client';
import { qk } from '../../api/keys';
import type { Client, ClientRole, PairingCode } from '../../api/types';
import { ActionButton, Empty, ErrorNote, Loading } from '../../components/bits';
import { ago, dateOnly, dateTime, time } from '../../format';
import { CLIENT_ROLE_HELP, CLIENT_ROLE_LABEL, t } from '../../i18n';

export function PairingQr({ pairing }: { pairing: Pick<PairingCode, 'code' | 'expires_at' | 'qr_payload'> & Partial<PairingCode> }) {
  const [src, setSrc] = useState<string | null>(null);
  const payload = parseQrPayload(pairing.qr_payload);
  useEffect(() => {
    let alive = true;
    QRCode.toDataURL(pairing.qr_payload, { errorCorrectionLevel: 'M', margin: 1, width: 480, color: { dark: '#1C1B19', light: '#FFFFFF' } })
      .then((url) => alive && setSrc(url))
      .catch(() => alive && setSrc(null));
    return () => {
      alive = false;
    };
  }, [pairing.qr_payload]);
  const role = pairing.role ?? payload?.role;
  return (
    <div className="row" style={{ alignItems: 'flex-start', gap: 20 }}>
      <div className="qr-box">{src ? <img src={src} alt={`QR-code om de telefoon te koppelen met code ${pairing.code}`} /> : <span className="muted">QR-code maken…</span>}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 200, flex: 1 }}>
        <span className="small muted">Of vul deze code in de app in:</span>
        <span className="code" data-testid="pair-code">{pairing.code}</span>
        <span className="small muted">Geldig tot {time(pairing.expires_at)} · eenmalig</span>
        {payload && (
          <dl className="facts" style={{ gridTemplateColumns: '1fr' }}>
            {role && <div><dt>Rechten</dt><dd>{t(CLIENT_ROLE_LABEL, role)}</dd></div>}
            <div><dt>Thuis (lokaal)</dt><dd>{payload.lan}</dd></div>
            <div><dt>Onderweg (tunnel)</dt><dd>{payload.remote ?? 'niet ingesteld'}</dd></div>
          </dl>
        )}
      </div>
    </div>
  );
}

const ROLES: ClientRole[] = ['control', 'viewer', 'admin'];

/** New pairing code with a role picker (control by default; admin needs the password). */
export function PairPhone() {
  const [label, setLabel] = useState('');
  const [role, setRole] = useState<ClientRole>('control');
  const [password, setPassword] = useState('');
  const create = useMutation({
    mutationFn: () => api.createPairingCode({ label: label.trim() || undefined, role, password: role === 'admin' ? password : undefined }),
    onSuccess: () => setPassword(''),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  const addressProblem = isApiError(create.error) && ['lan_url_required', 'lan_url_invalid', 'remote_url_invalid'].includes(create.error.code);
  return (
    <div className="stack">
      <h3>Telefoon koppelen</h3>
      <p className="small muted" style={{ margin: 0 }}>
        Open de Android-app en scan de QR-code. Elke telefoon krijgt een eigen sleutel die 180 dagen geldig is (de app vernieuwt hem zelf) en die je hier kunt intrekken.
      </p>
      <form className="stack" onSubmit={submit} aria-label="Koppelcode maken">
        <div className="form-grid">
          <label className="field">Naam (optioneel)<input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Telefoon van …" maxLength={100} /></label>
        </div>
        <fieldset className="choice">
          <legend>Wat mag deze telefoon?</legend>
          {ROLES.map((r) => (
            <label key={r} className={`choice-card${role === r ? ' selected' : ''}`}>
              <input type="radio" name="phone-role" value={r} checked={role === r} onChange={() => setRole(r)} />
              <span>
                <strong>{CLIENT_ROLE_LABEL[r]}</strong>
                <span className="small muted">{CLIENT_ROLE_HELP[r]}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {role === 'admin' && (
          <div className="form-grid">
            <label className="field">
              Je eigen wachtwoord (verplicht voor beheerrechten)
              <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
          </div>
        )}
        <div>
          <ActionButton type="submit" className="btn primary" disabled={create.isPending || (role === 'admin' && !password)}>QR-code maken</ActionButton>
        </div>
      </form>
      {create.error && <ErrorNote error={create.error} />}
      {addressProblem && <p className="small" style={{ margin: 0 }}><Link to="/systeem#adressen">Thuisadres instellen op Systeem › Adressen</Link></p>}
      {create.data && <PairingQr pairing={create.data} />}
    </div>
  );
}

function clientState(c: Client): { text: string; cls: string } {
  if (c.revoked) return { text: 'ingetrokken', cls: 'neutral' };
  if (c.expired) return { text: 'verlopen', cls: 'neutral' };
  return { text: 'actief', cls: 'ok' };
}

export function PhoneList() {
  const qc = useQueryClient();
  const clients = useQuery({ queryKey: qk.clients, queryFn: api.clients });
  const revoke = useMutation({ mutationFn: api.revokeClient, onSuccess: () => qc.invalidateQueries({ queryKey: qk.clients }) });
  const list = Array.isArray(clients.data) ? clients.data : [];
  return (
    <div className="stack">
      <h3>Gekoppelde telefoons</h3>
      {clients.isLoading && <Loading />}
      {clients.error && <ErrorNote error={clients.error} />}
      {clients.data && list.length === 0 && <Empty>Nog geen telefoons gekoppeld.</Empty>}
      {list.length > 0 && (
        <div className="table-wrap">
          <table className="stack-mobile">
            <thead><tr><th scope="col">Telefoon</th><th scope="col">Rechten</th><th scope="col">Gekoppeld</th><th scope="col">Laatst gebruikt</th><th scope="col">Verloopt</th><th scope="col"><span className="sr-only">Acties</span></th></tr></thead>
            <tbody>
              {list.map((c) => {
                const st = clientState(c);
                return (
                  <tr key={c.id}>
                    <td data-label="Telefoon">{c.name}{c.user && <div className="small muted">account {c.user}</div>}<span className={`pill ${st.cls}`}>{st.text}</span></td>
                    <td data-label="Rechten">{t(CLIENT_ROLE_LABEL, c.role)}</td>
                    <td data-label="Gekoppeld">{dateTime(c.created_at)}</td>
                    <td data-label="Laatst gebruikt">{ago(c.last_used_at)}</td>
                    <td data-label="Verloopt">{c.expires_at ? dateOnly(c.expires_at) : '–'}</td>
                    <td>
                      {!c.revoked && (
                        <ActionButton className="btn danger small" aria-label={`Toegang van ${c.name} intrekken`} disabled={revoke.isPending} onClick={() => { if (window.confirm(`Toegang van ${c.name} intrekken? De telefoon wordt direct afgemeld.`)) revoke.mutate(c.id); }}>
                          Intrekken
                        </ActionButton>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {revoke.error && <ErrorNote error={revoke.error} />}
    </div>
  );
}

export function PhonesPanel() {
  return (
    <div className="stack">
      <PairPhone />
      <hr />
      <PhoneList />
    </div>
  );
}
