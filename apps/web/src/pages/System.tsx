import { useEffect, useState, type FormEvent } from 'react';
import QRCode from 'qrcode';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, parseQrPayload } from '../api/client';
import type { PairingCode } from '../api/types';
import { useIsAdmin } from '../user';
import { useHome } from '../state';
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
  const [passphrase, setPassphrase] = useState('');
  const [includeSecrets, setIncludeSecrets] = useState(true);
  const backups = useQuery({ queryKey: ['backups'], queryFn: api.backups });
  const create = useMutation({
    mutationFn: () => api.createBackup({ passphrase, include_secrets: includeSecrets }),
    onSuccess: () => {
      setPassphrase('');
      qc.invalidateQueries({ queryKey: ['backups'] });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (passphrase.length >= 12) create.mutate();
  };
  return (
    <section aria-labelledby="bk-h" className="card">
      <h2 id="bk-h">Back-ups</h2>
      <p className="small muted" style={{ margin: 0 }}>Een back-up is versleuteld met je wachtwoordzin. Zonder die zin kan niemand (ook jij niet) hem terugzetten. Bewaar hem dus goed.</p>
      <form className="form-grid" onSubmit={submit} aria-label="Back-up maken">
        <label className="field">Wachtwoordzin (min. 12 tekens)
          <input type="password" autoComplete="new-password" minLength={12} required value={passphrase} onChange={(e) => setPassphrase(e.target.value)} />
        </label>
        <label className="row small" style={{ gap: 8, minHeight: 44 }}>
          <input type="checkbox" checked={includeSecrets} onChange={(e) => setIncludeSecrets(e.target.checked)} style={{ width: 20, height: 20 }} />
          Geheimen meenemen
        </label>
        <ActionButton type="submit" className="btn primary" disabled={create.isPending || passphrase.length < 12}>{create.isPending ? 'Bezig…' : 'Nu back-up maken'}</ActionButton>
      </form>
      {!includeSecrets && <div className="note warn">Zonder geheimen moet je na herstel de Hue Bridge en HomeWizard-apparaten opnieuw koppelen.</div>}
      {create.error && <ErrorNote error={create.error} />}
      {create.data && (
        <div className="note info" role="status">
          Back-up gemaakt: {create.data.filename} · {create.data.size_bytes !== null ? `${(create.data.size_bytes / 1_048_576).toLocaleString('nl-NL', { maximumFractionDigits: 1 })} MB` : ''}
          {create.data.detail?.tables && <> · {Object.values(create.data.detail.tables).reduce((a, b) => a + b, 0).toLocaleString('nl-NL')} records in {Object.keys(create.data.detail.tables).length} tabellen</>}
        </div>
      )}
      {backups.isLoading && <Loading />}
      {backups.data?.length === 0 && <Empty>Nog geen back-ups.</Empty>}
      {backups.data && backups.data.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Moment</th><th>Soort</th><th>Status</th><th>Grootte</th><th>Checksum</th><th><span className="sr-only">Download</span></th></tr></thead>
            <tbody>
              {backups.data.map((b) => (
                <tr key={b.id}>
                  <td>{dateTime(b.created_at)}<div className="small muted">{b.filename}</div></td>
                  <td>{b.kind}{b.detail?.secrets_included === false ? <div className="small muted">zonder geheimen</div> : null}</td>
                  <td>{b.status}</td>
                  <td>{b.size_bytes !== null ? `${(b.size_bytes / 1_048_576).toLocaleString('nl-NL', { maximumFractionDigits: 1 })} MB` : '–'}</td>
                  <td className="small" style={{ fontFamily: 'ui-monospace, monospace' }} title={b.checksum ?? undefined}>{b.checksum ? b.checksum.slice(0, 12) + '…' : '–'}</td>
                  <td><a className="btn" href={api.backupDownloadUrl(b.id)} download aria-label={`Back-up ${b.filename ?? ''} downloaden`}>Downloaden</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function SafeModeCard() {
  const qc = useQueryClient();
  const release = useMutation({
    mutationFn: api.releaseSafeMode,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['system'] });
      qc.invalidateQueries({ queryKey: ['overview'] });
    },
  });
  return (
    <section aria-labelledby="safe-h" className="card" style={{ borderColor: 'var(--warn-dot)', background: 'var(--warn-bg)', color: 'var(--warn-ink)' }}>
      <h2 id="safe-h">Veilige modus actief</h2>
      <p style={{ margin: 0, fontSize: 14 }}>Na een herstel of verhuizing stuurt de app geen commando's naar apparaten. Controleer onder Apparaten of elke rol aan het juiste apparaat hangt en test ze. Hef de veilige modus daarna op.</p>
      <div><ActionButton className="btn primary" disabled={release.isPending} onClick={() => { if (window.confirm('Kloppen alle apparaatkoppelingen? Daarna mag de app weer schakelen.')) release.mutate(); }}>Apparaten kloppen — veilige modus opheffen</ActionButton></div>
      {release.error && <ErrorNote error={release.error} />}
    </section>
  );
}

function AddressSettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['settings'], queryFn: api.settings });
  const [form, setForm] = useState<{ lan_url: string; remote_url: string } | null>(null);
  const values = form ?? { lan_url: q.data?.lan_url ?? '', remote_url: q.data?.remote_url ?? '' };
  const save = useMutation({
    mutationFn: () => api.putSettings({ lan_url: values.lan_url.trim() || null, remote_url: values.remote_url.trim() || null }),
    onSuccess: (s) => {
      qc.setQueryData(['settings'], s);
      setForm(null);
    },
  });
  return (
    <section aria-labelledby="adr-h" className="card">
      <h2 id="adr-h">Adressen</h2>
      <p className="small muted" style={{ margin: 0 }}>Deze adressen komen in de QR-code voor de Android-app. Het lokale adres gebruikt de app thuis. Het externe adres is je Cloudflare-tunneladres (bijv. https://huis.jouwdomein.nl); laat leeg als je geen tunnel gebruikt.</p>
      {q.isLoading ? <Loading /> : (
        <form className="form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <label className="field">Lokaal adres<input type="url" placeholder="http://192.168.1.20:8080" value={values.lan_url} onChange={(e) => setForm({ ...values, lan_url: e.target.value })} /></label>
          <label className="field">Extern adres (Cloudflare-tunnel)<input type="url" placeholder="https://huis.voorbeeld.nl" value={values.remote_url} onChange={(e) => setForm({ ...values, remote_url: e.target.value })} /></label>
          <ActionButton type="submit" className="btn primary" disabled={save.isPending || !form}>Opslaan</ActionButton>
        </form>
      )}
      {save.isSuccess && !form && <span className="small muted" role="status">Opgeslagen.</span>}
      {(q.error || save.error) && <ErrorNote error={q.error ?? save.error} />}
    </section>
  );
}

const TIMEZONES = ['Europe/Amsterdam', 'Europe/Brussels', 'Europe/Berlin', 'Europe/London', 'Europe/Paris', 'Europe/Madrid', 'UTC'];

function Homes() {
  const qc = useQueryClient();
  const { homeId, setHomeId } = useHome();
  const homes = useQuery({ queryKey: ['homes'], queryFn: api.homes });
  const [name, setName] = useState('');
  const [tz, setTz] = useState('Europe/Amsterdam');
  const create = useMutation({
    mutationFn: () => api.createHome({ name: name.trim(), timezone: tz }),
    onSuccess: (h) => {
      setName('');
      qc.invalidateQueries({ queryKey: ['homes'] });
      setHomeId(h.id);
    },
  });
  const archive = useMutation({ mutationFn: api.archiveHome, onSuccess: () => qc.invalidateQueries({ queryKey: ['homes'] }) });
  return (
    <section aria-labelledby="home-h" className="card">
      <h2 id="home-h">Woningen</h2>
      <p className="small muted" style={{ margin: 0 }}>Verhuisd? Maak een nieuwe woning aan en archiveer de oude. De oude geschiedenis blijft bewaard en apart van de nieuwe woning.</p>
      {homes.data && (
        <ul className="meter-list">
          {homes.data.map((h) => (
            <li key={h.id}>
              <span><strong>{h.name}</strong><span className="small muted"> · {h.timezone}{h.active ? '' : ' · gearchiveerd'}{h.id === homeId ? ' · geselecteerd' : ''}</span></span>
              {h.active && (
                <ActionButton className="btn danger" disabled={archive.isPending} onClick={() => { if (window.confirm(`Woning "${h.name}" archiveren? Gebruik dit als je verhuisd bent. De geschiedenis blijft bewaard, maar de woning wordt alleen-lezen.`)) archive.mutate(h.id); }}>
                  Archiveren (verhuisd)
                </ActionButton>
              )}
            </li>
          ))}
        </ul>
      )}
      <form className="form-grid" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(); }} aria-label="Nieuwe woning">
        <label className="field">Naam<input required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} placeholder="Nieuw huis" /></label>
        <label className="field">Tijdzone
          <select value={tz} onChange={(e) => setTz(e.target.value)}>{TIMEZONES.map((t) => <option key={t}>{t}</option>)}</select>
        </label>
        <ActionButton type="submit" className="btn primary" disabled={create.isPending}>Nieuwe woning</ActionButton>
      </form>
      {(create.error || archive.error) && <ErrorNote error={create.error ?? archive.error} />}
    </section>
  );
}

export function SystemPage() {
  const isAdmin = useIsAdmin();
  const status = useQuery({ queryKey: ['system'], queryFn: api.systemStatus, refetchInterval: 60_000 });
  const safeMode = status.data?.checks.some((c) => c.key === 'safe_mode');
  return (
    <>
      <header className="page-head"><div><span className="eyebrow">Beheer</span><h1>Systeem</h1></div></header>
      {safeMode && isAdmin && <SafeModeCard />}
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
          <AddressSettings />
          <PhonePairing />
          <Backups />
          <Homes />
        </>
      ) : (
        <p className="muted small">Instellingen, telefoons koppelen en back-ups zijn alleen beschikbaar voor beheerders.</p>
      )}
    </>
  );
}
