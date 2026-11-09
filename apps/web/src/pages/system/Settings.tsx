import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../../api/client';
import { qk } from '../../api/keys';
import type { NotifySettings as NotifyView, SystemSettings } from '../../api/types';
import { ActionButton, ErrorNote, Loading } from '../../components/bits';
import { HomeForm } from '../../components/NoHome';
import { useHome } from '../../state';

/** Home address (QR code for phones) and the tunnel address (verbeterlijst 17). id="adressen". */
export function AddressSettings({ headingLevel = 2 }: { headingLevel?: 2 | 3 }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: qk.settings, queryFn: api.settings });
  const [form, setForm] = useState<{ lan_url: string; remote_url: string } | null>(null);
  const data = q.data && !Array.isArray(q.data) ? q.data : null;
  const values = form ?? { lan_url: data?.lan_url ?? '', remote_url: data?.remote_url ?? '' };
  const save = useMutation({
    mutationFn: () => api.putSettings({ lan_url: values.lan_url.trim() || null, remote_url: values.remote_url.trim() || null }),
    onSuccess: (s) => {
      qc.setQueryData<SystemSettings>(qk.settings, s);
      qc.invalidateQueries({ queryKey: qk.preflight });
      setForm(null);
    },
  });
  const H = headingLevel === 2 ? 'h2' : 'h3';
  return (
    <section aria-labelledby="adr-h" className="card" id="adressen">
      <H id="adr-h">Adressen</H>
      <p className="small muted" style={{ margin: 0 }}>
        Deze adressen komen in de QR-code voor de Android-app. Het <strong>thuisadres</strong> is het adres van de NAS in je thuisnetwerk
        (bijvoorbeeld http://192.168.1.20:8088); zonder thuisadres kun je geen telefoon koppelen. Het <strong>adres buiten huis</strong> is je
        Cloudflare-tunneladres (moet met https:// beginnen); laat het leeg als je geen tunnel gebruikt.
      </p>
      {q.isLoading ? (
        <Loading />
      ) : (
        <form className="form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }} aria-label="Adressen">
          <label className="field">
            Thuisadres
            <input type="url" inputMode="url" placeholder="http://192.168.1.20:8088" value={values.lan_url} onChange={(e) => setForm({ ...values, lan_url: e.target.value })} />
          </label>
          <label className="field">
            Adres buiten huis (Cloudflare-tunnel)
            <input type="url" inputMode="url" placeholder="https://huis.voorbeeld.nl" value={values.remote_url} onChange={(e) => setForm({ ...values, remote_url: e.target.value })} />
          </label>
          <ActionButton type="submit" className="btn primary" disabled={save.isPending || !form}>Opslaan</ActionButton>
        </form>
      )}
      {save.isSuccess && !form && <span className="small muted" role="status">Opgeslagen.</span>}
      {(q.error || save.error) && <ErrorNote error={q.error ?? save.error} />}
    </section>
  );
}

function isNotify(x: unknown): x is NotifyView {
  return typeof x === 'object' && x !== null && !Array.isArray(x) && 'configured' in x;
}

/** The reason the server gave for an unusable push address (422), without the "url: " field prefix. */
export function notifyProblem(err: unknown): string | null {
  if (!(err instanceof ApiError) || err.status !== 422) return null;
  const first = (err.detail.errors as unknown[] | undefined)?.[0] as { msg?: unknown; type?: unknown } | undefined;
  if (first?.type === 'string_too_long') return 'Het adres is te lang (maximaal 500 tekens).';
  const msg = typeof first?.msg === 'string' ? first.msg : err.message;
  return msg.replace(/^Value error, /, '').replace(/^url: /, '') || 'Dit adres kan niet gebruikt worden.';
}

/** Push message outside the app (e.g. ntfy) when the worker or the backups stop (verbeterlijst 28).
 * Web session only; the address is a secret, so only its server is shown after saving (id="meldingen"). */
export function NotifySettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: qk.notify, queryFn: api.notify });
  const n = isNotify(q.data) ? q.data : null;
  const [url, setUrl] = useState('');
  const test = useMutation({ mutationFn: api.testNotify });
  const save = useMutation({
    mutationFn: (u: string | null) => api.putNotify(u),
    onSuccess: (v) => {
      qc.setQueryData<NotifyView>(qk.notify, v);
      setUrl('');
      test.reset(); // an old test result was for the previous address
    },
  });
  const problem = notifyProblem(save.error);
  const events = n?.events?.length
    ? n.events
    : ['De achtergronddienst reageert niet meer (en weer wel)', 'Automatische back-ups mislukken of de laatste is te oud (en weer in orde)'];
  return (
    <section aria-labelledby="notify-h" className="card" id="meldingen">
      <h2 id="notify-h">Meldingen buiten de app</h2>
      <p className="small muted break" style={{ margin: 0 }}>
        Stopt de achtergronddienst, dan kan de app zelf niets meer melden. Daarom kan de server ook een pushbericht sturen naar een adres
        van jou, bijvoorbeeld een <strong>ntfy</strong>-onderwerp (https://ntfy.sh/<em>geheim-onderwerp</em>, of je eigen ntfy-server in het
        thuisnetwerk) of een andere webhook. Kies een lang, onraadbaar onderwerp: wie het adres kent, kan meelezen. Je krijgt één bericht
        als er iets misgaat en één als het weer in orde is.
      </p>
      {q.isLoading ? (
        <Loading />
      ) : n ? (
        <>
          <dl className="facts">
            <div>
              <dt>Status</dt>
              <dd>
                {n.configured
                  ? n.source === 'env' ? 'Aan (ingesteld op de server, SHC_NOTIFY_URL)' : 'Aan (ingesteld in de app)'
                  : n.error ? 'Uit (opgeslagen adres onbruikbaar)' : 'Uit (geen adres)'}
              </dd>
            </div>
            <div><dt>Adres</dt><dd className="break">{n.url_hint ?? '–'}</dd></div>
          </dl>
          {n.error && (
            // The address saved in the app does not decrypt with this server's master key (restore on a
            // new NAS, key change): the server ignores it until it is entered again (verbeterlijst 22/28).
            <div id="notify-stored-error" className="note warn">
              <strong>Opgeslagen push-adres onbruikbaar.</strong> {n.error}
            </div>
          )}
          <div className="small">
            Een bericht bij:
            <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{events.map((ev) => <li key={ev}>{ev}</li>)}</ul>
          </div>
          <form className="form-grid" aria-label="Push-adres instellen" onSubmit={(e) => { e.preventDefault(); if (url.trim()) save.mutate(url.trim()); }}>
            <label className="field">
              {n.source === 'app' ? 'Nieuw push-adres' : 'Push-adres'}
              <input
                type="url"
                inputMode="url"
                autoComplete="off"
                spellCheck={false}
                maxLength={500}
                placeholder="https://ntfy.sh/geheim-onderwerp"
                value={url}
                onChange={(e) => {
                  setUrl(e.target.value);
                  if (save.isError) save.reset(); // the refusal was for the previous text
                }}
                aria-invalid={problem ? true : undefined}
                aria-describedby={[problem && 'notify-problem', n.error && 'notify-stored-error'].filter(Boolean).join(' ') || undefined}
              />
            </label>
            <ActionButton type="submit" className="btn primary" disabled={save.isPending || !url.trim()}>Opslaan</ActionButton>
          </form>
          <div className="row">
            <ActionButton className="btn" disabled={!n.configured || test.isPending} onClick={() => test.mutate()}>
              {test.isPending ? 'Versturen…' : 'Testbericht sturen'}
            </ActionButton>
            {(n.source === 'app' || n.error) && (
              <ActionButton
                className="btn danger"
                disabled={save.isPending}
                onClick={() => { if (window.confirm('Push-adres verwijderen? Er worden dan geen berichten buiten de app meer verstuurd (behalve naar een adres dat op de server is ingesteld).')) save.mutate(null); }}
              >
                Adres verwijderen
              </ActionButton>
            )}
          </div>
          {n.source === 'env' && (
            <p className="small muted" style={{ margin: 0 }}>Een adres dat je hier opslaat, gaat vóór het adres van de server.</p>
          )}
        </>
      ) : null}
      {problem ? (
        <div id="notify-problem" className="note error" role="alert">{problem}</div>
      ) : (
        (q.error || save.error) && <ErrorNote error={q.error ?? save.error} />
      )}
      {save.isSuccess && !url && <span className="small muted" role="status">{save.variables === null ? 'Adres verwijderd.' : 'Opgeslagen.'}</span>}
      {test.data && (
        test.data.ok ? (
          <div className="note info" role="status">Testbericht verstuurd. Kijk of het is aangekomen.</div>
        ) : (
          <div className="note error" role="alert">Testbericht niet verstuurd: {test.data.error ?? 'onbekende fout'}</div>
        )
      )}
      {test.error && <ErrorNote error={test.error} />}
    </section>
  );
}

/** Shown after a restore or move: no device commands until the admin confirms (id="veilige-modus"). */
export function SafeModeCard() {
  const qc = useQueryClient();
  const release = useMutation({
    mutationFn: api.releaseSafeMode,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.system });
      qc.invalidateQueries({ queryKey: qk.settings });
      qc.invalidateQueries({ queryKey: ['overview'] });
      qc.invalidateQueries({ queryKey: qk.restoreCompare });
    },
  });
  return (
    <section aria-labelledby="safe-h" className="card attention" id="veilige-modus">
      <h2 id="safe-h">Veilige modus actief</h2>
      <p style={{ margin: 0, fontSize: 14 }}>
        Na een herstel of verhuizing stuurt de app geen commando's naar apparaten. Bekijk hieronder bij <a href="#herstel">Herstellen en verhuizen</a> per
        apparaat en functie wat werkt, en test de apparaten onder <Link to="/apparaten">Apparaten</Link> (knop Testen leest alleen uit). Hef de veilige modus daarna op.
      </p>
      <div>
        <ActionButton className="btn primary" disabled={release.isPending} onClick={() => { if (window.confirm('Kloppen alle apparaatkoppelingen? Daarna mag de app weer schakelen.')) release.mutate(); }}>
          Apparaten kloppen — veilige modus opheffen
        </ActionButton>
      </div>
      {release.error && <ErrorNote error={release.error} />}
    </section>
  );
}

/** Homes: create a new one, archive the old one after moving (id="woningen"). */
export function Homes() {
  const qc = useQueryClient();
  const { homeId } = useHome();
  const homes = useQuery({ queryKey: qk.homes, queryFn: api.homes });
  const archive = useMutation({ mutationFn: api.archiveHome, onSuccess: () => qc.invalidateQueries({ queryKey: qk.homes }) });
  const list = Array.isArray(homes.data) ? homes.data : [];
  return (
    <section aria-labelledby="home-h" className="card" id="woningen">
      <h2 id="home-h">Woningen</h2>
      <p className="small muted" style={{ margin: 0 }}>
        Verhuisd? Maak een nieuwe woning aan en archiveer de oude, of zet een back-up terug als nieuwe woning bij <a href="#herstel">Herstellen en verhuizen</a>.
        De oude geschiedenis blijft bewaard en apart van de nieuwe woning; de koppelingen van een gearchiveerde woning gaan uit.
      </p>
      {list.length > 0 && (
        <ul className="meter-list">
          {list.map((h) => (
            <li key={h.id}>
              <span><strong>{h.name}</strong><span className="small muted"> · {h.timezone}{h.active ? '' : ' · gearchiveerd'}{h.id === homeId ? ' · geselecteerd' : ''}</span></span>
              {h.active && (
                <ActionButton className="btn danger" disabled={archive.isPending} onClick={() => { if (window.confirm(`Woning "${h.name}" archiveren? Gebruik dit als je verhuisd bent. De geschiedenis blijft bewaard, maar de woning wordt alleen-lezen en de koppelingen gaan uit.`)) archive.mutate(h.id); }}>
                  Archiveren (verhuisd)
                </ActionButton>
              )}
            </li>
          ))}
        </ul>
      )}
      <h3>Nieuwe woning</h3>
      <HomeForm submitLabel="Nieuwe woning" idPrefix="newhome" />
      {archive.error && <ErrorNote error={archive.error} />}
    </section>
  );
}
