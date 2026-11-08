import { useEffect, useState } from 'react';
import QRCode from 'qrcode';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, parseQrPayload } from '../api/client';
import type { PairingCode } from '../api/types';
import { useIsAdmin } from '../user';
import { ActionButton, Dot, Empty, ErrorNote, Loading } from '../components/bits';
import { ago, dateTime, time } from '../format';

export function PairingQr({ pairing }: { pairing: PairingCode }) {
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
  return (
    <div className="row" style={{ alignItems: 'flex-start', gap: 20 }}>
      <div className="qr-box">{src ? <img src={src} alt={`QR-code om de telefoon te koppelen met code ${pairing.code}`} /> : <span className="muted">QR-code maken…</span>}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 200, flex: 1 }}>
        <span className="small muted">Of vul deze code in de app in:</span>
        <span className="code" data-testid="pair-code">{pairing.code}</span>
        <span className="small muted">Geldig tot {time(pairing.expires_at)} · eenmalig</span>
        {payload && (
          <dl className="facts" style={{ gridTemplateColumns: '1fr' }}>
            <div><dt>Thuis (lokaal)</dt><dd>{payload.lan}</dd></div>
            <div><dt>Onderweg (tunnel)</dt><dd>{payload.remote ?? 'niet ingesteld'}</dd></div>
          </dl>
        )}
      </div>
    </div>
  );
}

function PhonePairing() {
  const qc = useQueryClient();
  const [label, setLabel] = useState('');
  const clients = useQuery({ queryKey: ['clients'], queryFn: api.clients });
  const create = useMutation({ mutationFn: () => api.createPairingCode(label || undefined) });
  const revoke = useMutation({ mutationFn: api.revokeClient, onSuccess: () => qc.invalidateQueries({ queryKey: ['clients'] }) });
  return (
    <section aria-labelledby="tel-h" className="card">
      <h2 id="tel-h">Telefoon koppelen</h2>
      <p className="small muted" style={{ margin: 0 }}>Open de Android-app en scan de QR-code. Elke telefoon krijgt een eigen sleutel die je hier kunt intrekken.</p>
      <div className="form-grid">
        <label className="field">Naam (optioneel)<input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Telefoon van …" /></label>
        <ActionButton className="btn primary" disabled={create.isPending} onClick={() => create.mutate()}>QR-code maken</ActionButton>
      </div>
      {create.error && <ErrorNote error={create.error} />}
      {create.data && <PairingQr pairing={create.data} />}
      <h3 style={{ fontSize: 14, marginTop: 8 }}>Gekoppelde telefoons</h3>
      {clients.isLoading && <Loading />}
      {clients.data?.length === 0 && <Empty>Nog geen telefoons gekoppeld.</Empty>}
      {clients.data && clients.data.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Naam</th><th>Gekoppeld</th><th>Laatst gebruikt</th><th><span className="sr-only">Acties</span></th></tr></thead>
            <tbody>
              {clients.data.map((c) => (
                <tr key={c.id}>
                  <td>{c.name}{c.revoked && <span className="small muted"> · ingetrokken</span>}</td>
                  <td>{dateTime(c.created_at)}</td>
                  <td>{ago(c.last_used_at)}</td>
                  <td>{!c.revoked && <ActionButton className="btn danger" aria-label={`Toegang van ${c.name} intrekken`} onClick={() => { if (window.confirm(`Toegang van ${c.name} intrekken?`)) revoke.mutate(c.id); }}>Intrekken</ActionButton>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Backups() {
  const qc = useQueryClient();
  const backups = useQuery({ queryKey: ['backups'], queryFn: api.backups });
  const create = useMutation({ mutationFn: api.createBackup, onSuccess: () => qc.invalidateQueries({ queryKey: ['backups'] }) });
  return (
    <section aria-labelledby="bk-h" className="card">
      <div className="section-head">
        <h2 id="bk-h">Back-ups</h2>
        <ActionButton className="btn" disabled={create.isPending} onClick={() => create.mutate()}>{create.isPending ? 'Bezig…' : 'Nu back-up maken'}</ActionButton>
      </div>
      {create.error && <ErrorNote error={create.error} />}
      {backups.isLoading && <Loading />}
      {backups.data?.length === 0 && <Empty>Nog geen back-ups.</Empty>}
      {backups.data && backups.data.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Moment</th><th>Soort</th><th>Status</th><th>Grootte</th><th>Checksum</th></tr></thead>
            <tbody>
              {backups.data.map((b) => (
                <tr key={b.id}>
                  <td>{dateTime(b.created_at)}</td>
                  <td>{b.kind}</td>
                  <td>{b.status}</td>
                  <td>{b.size_bytes !== null ? `${(b.size_bytes / 1_048_576).toLocaleString('nl-NL', { maximumFractionDigits: 1 })} MB` : '–'}</td>
                  <td className="small" style={{ fontFamily: 'ui-monospace, monospace' }}>{b.checksum ? b.checksum.slice(0, 12) + '…' : '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function SystemPage() {
  const isAdmin = useIsAdmin();
  const status = useQuery({ queryKey: ['system'], queryFn: api.systemStatus, refetchInterval: 60_000 });
  return (
    <>
      <header className="page-head"><div><span className="eyebrow">Beheer</span><h1>Systeem</h1></div></header>
      <section aria-labelledby="st-h" className="section">
        <h2 id="st-h">Status</h2>
        {status.isLoading && <Loading />}
        {status.error && <ErrorNote error={status.error} />}
        <div className="grid-status">
          {status.data?.checks.map((c) => (
            <div key={c.key} className="card tight live">
              <span className="card-sub">{c.label}</span>
              <span className={`state-line ${c.state}`}><Dot state={c.state} />{c.detail}</span>
            </div>
          ))}
        </div>
      </section>
      {isAdmin ? (
        <>
          <PhonePairing />
          <Backups />
        </>
      ) : (
        <p className="muted small">Koppelen van telefoons en back-ups zijn alleen beschikbaar voor beheerders.</p>
      )}
    </>
  );
}
