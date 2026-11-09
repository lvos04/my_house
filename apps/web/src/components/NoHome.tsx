import { useMemo, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { Home } from '../api/types';
import { useHome } from '../state';
import { useCan } from '../user';
import { browserTimeZone, timeZones } from '../format';
import { ActionButton, ErrorNote } from './bits';

export const WELCOME_KEY = 'shc.nextSteps';

function rememberWelcome(homeId: string) {
  try {
    localStorage.setItem(WELCOME_KEY, homeId);
  } catch {
    /* storage blocked: the card is just not shown */
  }
}

/** Name + time zone (defaults to the browser's IANA zone). Selects the new home when created. */
export function HomeForm({ onCreated, submitLabel = 'Woning aanmaken', idPrefix = 'home' }: { onCreated?: (h: Home) => void; submitLabel?: string; idPrefix?: string }) {
  const qc = useQueryClient();
  const { setHomeId } = useHome();
  const zones = useMemo(() => timeZones(), []);
  const [name, setName] = useState('');
  const [tz, setTz] = useState(browserTimeZone);
  const create = useMutation({
    mutationFn: () => api.createHome({ name: name.trim(), timezone: tz }),
    onSuccess: (h) => {
      setName('');
      qc.setQueryData<Home[]>(qk.homes, (l) => [...(l ?? []).filter((x) => x.id !== h.id), h]);
      qc.invalidateQueries({ queryKey: qk.homes });
      setHomeId(h.id);
      onCreated?.(h);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim()) create.mutate();
  };
  return (
    <form className="form-grid" onSubmit={submit} aria-label="Woning aanmaken">
      <label className="field" htmlFor={`${idPrefix}-name`}>
        Naam van de woning
        <input id={`${idPrefix}-name`} required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} placeholder="Bijvoorbeeld: Thuis" autoComplete="off" />
      </label>
      <label className="field" htmlFor={`${idPrefix}-tz`}>
        Tijdzone
        <select id={`${idPrefix}-tz`} value={tz} onChange={(e) => setTz(e.target.value)}>
          {zones.map((z) => <option key={z} value={z}>{z.replace(/_/g, ' ')}</option>)}
        </select>
      </label>
      <ActionButton type="submit" className="btn primary" disabled={create.isPending || !name.trim()}>{create.isPending ? 'Bezig…' : submitLabel}</ActionButton>
      {create.error && <div style={{ gridColumn: '1 / -1' }}><ErrorNote error={create.error} /></div>}
    </form>
  );
}

/** Shown instead of the page while there is no home yet (verbeterlijst 1): never a dead end. */
export function NoHomeSetup() {
  const can = useCan();
  return (
    <section className="card" aria-labelledby="nohome-h">
      <div>
        <span className="eyebrow">Eerste stap</span>
        <h1 id="nohome-h" style={{ fontSize: 26 }}>Woning aanmaken</h1>
      </div>
      {can.admin ? (
        <>
          <p className="muted" style={{ margin: 0 }}>
            Er is nog geen woning. Geef je woning een naam en kies de tijdzone; schema's en energieperiodes volgen die tijdzone.
            Daarna stel je het thuisadres in en koppel je je apparaten.
          </p>
          <HomeForm onCreated={(h) => rememberWelcome(h.id)} />
          <p className="small muted" style={{ margin: 0 }}>
            Liever stap voor stap? <Link to="/installatie">Open de installatiewizard</Link>. Een back-up terugzetten kan via <Link to="/systeem#herstel">Systeem › Herstellen en verhuizen</Link>.
          </p>
        </>
      ) : (
        <p className="muted" style={{ margin: 0 }}>Er is nog geen woning. Vraag een beheerder om er een aan te maken. Hulp vind je op de <Link to="/help">hulppagina</Link>.</p>
      )}
    </section>
  );
}

/** After creating the first home: what to do next. Dismissible; shown until closed. */
export function NextSteps({ homeId }: { homeId: string }) {
  const [shown, setShown] = useState(() => {
    try {
      return localStorage.getItem(WELCOME_KEY) === homeId;
    } catch {
      return false;
    }
  });
  if (!shown) return null;
  const close = () => {
    try {
      localStorage.removeItem(WELCOME_KEY);
    } catch {
      /* ignore */
    }
    setShown(false);
  };
  return (
    <section className="card next-steps" aria-labelledby="next-h">
      <div className="section-head">
        <h2 id="next-h">Volgende stappen</h2>
        <button type="button" className="btn ghost" onClick={close}>Sluiten</button>
      </div>
      <p style={{ margin: 0 }}>Je woning is aangemaakt. Dit heeft de app nog nodig:</p>
      <ol className="steps-list">
        <li><Link to="/systeem#adressen">Stel het thuisadres in</Link> <span className="muted small">(Systeem › Adressen; nodig om telefoons te koppelen)</span></li>
        <li><Link to="/apparaten">Koppel je apparaten</Link> <span className="muted small">(Hue, HomeWizard, Gree, Zigbee-sensoren)</span></li>
        <li><Link to="/systeem#backups">Zet automatische back-ups aan</Link></li>
      </ol>
      <p className="small muted" style={{ margin: 0 }}>Of volg alles stap voor stap in de <Link to="/installatie">installatiewizard</Link>.</p>
    </section>
  );
}
