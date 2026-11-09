import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import { ErrorNote, Loading } from '../components/bits';
import { CheckIcon } from '../components/Icons';
import { HomeForm } from '../components/NoHome';
import { ProviderPair } from '../components/setup/ProviderPair';
import { ZigbeeSetup } from '../components/setup/ZigbeeSetup';
import { RoleAssign, SensorAssign } from '../components/setup/Assign';
import { useHome } from '../state';
import { useCan } from '../user';
import { CheckCard } from './system/Status';
import { AddressSettings } from './system/Settings';

export const INSTALL_KEY = 'shc.install';

export const INSTALL_STEPS = [
  { key: 'controle', title: 'Controles vooraf' },
  { key: 'woning', title: 'Woning' },
  { key: 'adressen', title: 'Adressen' },
  { key: 'hue', title: 'Philips Hue', optional: true },
  { key: 'homewizard', title: 'HomeWizard', optional: true },
  { key: 'gree', title: 'Gree airco', optional: true },
  { key: 'zigbee', title: 'Zigbee', optional: true },
  { key: 'sensoren', title: 'Sensoren toewijzen' },
  { key: 'meters', title: 'Meters en rollen' },
  { key: 'klaar', title: 'Klaar' },
] as const;
export type InstallStep = (typeof INSTALL_STEPS)[number]['key'];

export interface InstallProgress {
  step: InstallStep;
  done: InstallStep[];
}
export function loadInstall(): InstallProgress {
  try {
    const v = JSON.parse(localStorage.getItem(INSTALL_KEY) ?? 'null') as InstallProgress | null;
    if (v && INSTALL_STEPS.some((s) => s.key === v.step) && Array.isArray(v.done)) return v;
  } catch {
    /* ignore */
  }
  return { step: 'controle', done: [] };
}
function saveInstall(p: InstallProgress) {
  try {
    localStorage.setItem(INSTALL_KEY, JSON.stringify(p));
  } catch {
    /* storage blocked: progress is kept while the page is open */
  }
}

function Preflight() {
  const q = useQuery({ queryKey: qk.preflight, queryFn: api.preflight });
  const data = q.data && !Array.isArray(q.data) ? q.data : null;
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>
        De server controleert de achtergronddienst, Zigbee (MQTT en USB-dongle), of apparaten automatisch te vinden zijn, de klok, het thuisadres en de back-ups.
        Punten met Beperkt zijn geen blokkade; die los je in de volgende stappen op.
      </p>
      <div><button type="button" className="btn" onClick={() => q.refetch()} disabled={q.isFetching}>{q.isFetching ? 'Controleren…' : 'Opnieuw controleren'}</button></div>
      {q.isLoading && <Loading label="Controleren…" />}
      {q.error && <ErrorNote error={q.error} />}
      {data && (
        <>
          <div className={`note ${data.ready ? 'ok' : 'warn'}`} role="status">
            {data.ready ? 'Klaar om te installeren.' : 'Er is nog iets dat aandacht nodig heeft (Storing). De wizard helpt je verder.'}
          </div>
          <div className="grid-status wide">{data.checks.map((c) => <CheckCard key={c.key} check={c} />)}</div>
        </>
      )}
    </div>
  );
}

function HomeStep() {
  const homes = useQuery({ queryKey: qk.homes, queryFn: api.homes });
  const list = (Array.isArray(homes.data) ? homes.data : []).filter((h) => h.active);
  if (homes.isLoading) return <Loading titel="Installatiewizard" />;
  return (
    <div className="stack">
      {list.length > 0 ? (
        <p style={{ margin: 0 }}>Je woning: <strong>{list.map((h) => `${h.name} (${h.timezone})`).join(', ')}</strong>. Je kunt doorgaan.</p>
      ) : (
        <p style={{ margin: 0 }}>Geef je woning een naam en kies de tijdzone (meestal al goed ingevuld).</p>
      )}
      {list.length === 0 && <HomeForm idPrefix="wiz-home" />}
    </div>
  );
}

function NeedsHome({ homeId, children, go }: { homeId: string | null; children: (homeId: string) => ReactNode; go: () => void }) {
  if (!homeId)
    return (
      <div className="note warn" role="note">
        Maak eerst een woning aan. <button type="button" className="btn small" onClick={go}>Naar stap Woning</button>
      </div>
    );
  return <>{children(homeId)}</>;
}

/** /installatie: step-by-step first installation (verbeterlijst 43). Progress is kept in this browser;
 * reopen it any time from Systeem. */
export function InstallPage() {
  const can = useCan();
  const { homeId } = useHome();
  const [progress, setProgress] = useState<InstallProgress>(loadInstall);
  const headRef = useRef<HTMLHeadingElement>(null);
  const first = useRef(true);
  useEffect(() => saveInstall(progress), [progress]);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    headRef.current?.focus();
  }, [progress.step]);

  const idx = INSTALL_STEPS.findIndex((s) => s.key === progress.step);
  const step = INSTALL_STEPS[idx];
  const goto = (k: InstallStep) => setProgress((p) => ({ ...p, step: k }));
  const next = () =>
    setProgress((p) => ({
      step: INSTALL_STEPS[Math.min(idx + 1, INSTALL_STEPS.length - 1)].key,
      done: p.done.includes(p.step) ? p.done : [...p.done, p.step],
    }));
  const prev = () => goto(INSTALL_STEPS[Math.max(idx - 1, 0)].key);

  if (!can.admin)
    return (
      <>
        <header className="page-head"><div><span className="eyebrow">Installatie</span><h1>Installatiewizard</h1></div></header>
        <p className="muted">Alleen een beheerder kan de installatie doen.</p>
      </>
    );

  let body: ReactNode;
  switch (step.key) {
    case 'controle':
      body = <Preflight />;
      break;
    case 'woning':
      body = <HomeStep />;
      break;
    case 'adressen':
      body = <AddressSettings headingLevel={3} />;
      break;
    case 'hue':
    case 'homewizard':
    case 'gree':
      body = <NeedsHome homeId={homeId} go={() => goto('woning')}>{(h) => <ProviderPair key={step.key} provider={step.key} homeId={h} />}</NeedsHome>;
      break;
    case 'zigbee':
      body = <NeedsHome homeId={homeId} go={() => goto('woning')}>{(h) => <ZigbeeSetup homeId={h} />}</NeedsHome>;
      break;
    case 'sensoren':
      body = <NeedsHome homeId={homeId} go={() => goto('woning')}>{(h) => <SensorAssign homeId={h} />}</NeedsHome>;
      break;
    case 'meters':
      body = <NeedsHome homeId={homeId} go={() => goto('woning')}>{(h) => <RoleAssign homeId={h} />}</NeedsHome>;
      break;
    case 'klaar':
      body = (
        <div className="stack">
          <p style={{ margin: 0 }}>De basis staat. Handig om nu nog te doen:</p>
          <ul className="steps-list">
            <li><Link to="/systeem#backups">Automatische back-ups aanzetten</Link></li>
            <li><Link to="/account#telefoons">Een telefoon koppelen</Link></li>
            <li><Link to="/klimaat">Schema's per kamer instellen</Link></li>
            <li><Link to="/energie">Een energietarief invullen</Link></li>
          </ul>
          <p className="small muted" style={{ margin: 0 }}>Je kunt deze wizard altijd opnieuw openen via Systeem.</p>
          <div><Link className="btn primary" to="/">Naar het overzicht</Link></div>
        </div>
      );
      break;
  }

  return (
    <>
      <header className="page-head">
        <div><span className="eyebrow">Installatie</span><h1>Installatiewizard</h1></div>
        <Link className="btn small" to="/help">Hulp per merk</Link>
      </header>
      <div className="wizard">
        <nav aria-label="Stappen" className="wizard-steps">
          <ol>
            {INSTALL_STEPS.map((s, i) => {
              const done = progress.done.includes(s.key);
              return (
                <li key={s.key}>
                  <button type="button" className={`wizard-step${s.key === step.key ? ' current' : ''}${done ? ' done' : ''}`} aria-current={s.key === step.key ? 'step' : undefined} onClick={() => goto(s.key)}>
                    <span className="wizard-num" aria-hidden="true">{done ? <CheckIcon width={14} height={14} /> : i + 1}</span>
                    <span>{s.title}{done && <span className="sr-only"> (gedaan)</span>}</span>
                  </button>
                </li>
              );
            })}
          </ol>
        </nav>
        <section className="card wizard-body" aria-labelledby="wiz-h">
          <div>
            <span className="eyebrow">Stap {idx + 1} van {INSTALL_STEPS.length}{'optional' in step && step.optional ? ' · optioneel' : ''}</span>
            <h2 id="wiz-h" ref={headRef} tabIndex={-1}>{step.title}</h2>
          </div>
          {body}
          <div className="row wizard-nav">
            {idx > 0 && <button type="button" className="btn" onClick={prev}>Vorige</button>}
            {idx < INSTALL_STEPS.length - 1 && (
              <button type="button" className="btn primary" onClick={next}>{'optional' in step && step.optional ? 'Volgende (of overslaan)' : 'Volgende'}</button>
            )}
          </div>
        </section>
      </div>
    </>
  );
}
