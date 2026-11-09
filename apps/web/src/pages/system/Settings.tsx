import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { qk } from '../../api/keys';
import type { SystemSettings } from '../../api/types';
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
