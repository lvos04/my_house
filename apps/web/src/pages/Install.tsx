import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { BeleidsSlot } from '../components/peil/BeleidsSlot';
import { ConfirmDevice } from '../devices/CommissioningActions';
import { DeviceTestButton } from '../devices/DeviceTools';
import { commissioningText as text } from '../devices/commissioning-text';
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
  { key: 'meters', title: text('meters'), optional: true },
  { key: 'hue-lezen', title: text('hueRead'), optional: true },
  { key: 'hue-testen', title: text('hueTest'), optional: true },
  { key: 'gree', title: text('gree'), optional: true },
  { key: 'zigbee', title: 'Zigbee', optional: true },
  { key: 'sensoren', title: 'Sensoren toewijzen' },
  { key: 'klaar', title: 'Klaar' },
] as const;
export type InstallStep = (typeof INSTALL_STEPS)[number]['key'];

export interface InstallProgress {
  step: InstallStep;
  done: InstallStep[];
}
export function loadInstall(): InstallProgress {
  try {
    const v = JSON.parse(localStorage.getItem(INSTALL_KEY) ?? 'null') as { step: string; done: string[]; version?: number } | null;
    if (v && Array.isArray(v.done)) {
      const old = v.version !== 2;
      const savedStep = v.step === 'hue' ? 'hue-lezen' : v.step === 'homewizard' ? 'meters' : v.step;
      const step = INSTALL_STEPS.find((entry) => entry.key === savedStep)?.key ?? 'controle';
      const done = INSTALL_STEPS.map((entry) => entry.key).filter((key) =>
        v.done.includes(key) && key !== 'gree' && key !== 'hue-testen' && !(old && ['meters', 'hue-lezen'].includes(key)),
      );
      return { step, done };
    }
  } catch {
    /* ignore */
  }
  return { step: 'controle', done: [] };
}
function saveInstall(p: InstallProgress) {
  try {
    localStorage.setItem(INSTALL_KEY, JSON.stringify({ ...p, version: 2 }));
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

function HueTestStep({ homeId }: { homeId: string }) {
  const devices = useQuery({ queryKey: qk.devices(homeId), queryFn: () => api.devices(homeId) });
  const lamps = (Array.isArray(devices.data) ? devices.data : []).filter((d) =>
    d.provider === 'hue' && d.kind === 'light' && d.lifecycle === 'active',
  );
  return (
    <div className="grid gap-3">
      <p className="m-0">Controleer een lamp met de eigen Hue-bediening. Voer hier de leestest uit en bevestig daarna je controle.</p>
      {devices.isLoading && <Loading />}
      {devices.error && <ErrorNote error={devices.error} />}
      {devices.isSuccess && lamps.length === 0 && <p className="m-0">Geen Hue-lampen gekoppeld. Ga terug naar Hue uitlezen.</p>}
      {lamps.map((lamp) => (
        <Card key={lamp.id} className="grid gap-3">
          <h3 className="m-0 break-words">{lamp.name}</h3>
          <DeviceTestButton device={lamp} named />
          {lamp.compatibility === 'tested' && <p className="m-0">{text('tested')}</p>}
          <ConfirmDevice id={lamp.id} name={lamp.name} eligible={lamp.compatibility === 'untested'} named />
          {lamp.compatibility === 'unsupported' && <p className="m-0">{text('unsupportedHelp')}</p>}
        </Card>
      ))}
      {lamps.some((lamp) => lamp.compatibility === 'tested') && (
        <Link to="/verlichting">Controleer een bewuste bediening en de terugmelding bij Verlichting</Link>
      )}
    </div>
  );
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
      done: p.step === 'gree' || p.step === 'hue-testen' || p.done.includes(p.step) ? p.done : [...p.done, p.step],
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
    case 'hue-lezen':
      body = <NeedsHome homeId={homeId} go={() => goto('woning')}>{(h) => <ProviderPair provider="hue" homeId={h} />}</NeedsHome>;
      break;
    case 'hue-testen':
      body = <NeedsHome homeId={homeId} go={() => goto('woning')}>{(h) => <HueTestStep homeId={h} />}</NeedsHome>;
      break;
    case 'gree':
      body = <BeleidsSlot label={text('waiting')}>{text('d12Wait')}</BeleidsSlot>;
      break;
    case 'zigbee':
      body = <NeedsHome homeId={homeId} go={() => goto('woning')}>{(h) => <ZigbeeSetup homeId={h} />}</NeedsHome>;
      break;
    case 'sensoren':
      body = <NeedsHome homeId={homeId} go={() => goto('woning')}>{(h) => <SensorAssign homeId={h} />}</NeedsHome>;
      break;
    case 'meters':
      body = <NeedsHome homeId={homeId} go={() => goto('woning')}>{(h) => (
        <div className="grid gap-4">
          <ProviderPair provider="homewizard" homeId={h} />
          <RoleAssign homeId={h} />
        </div>
      )}</NeedsHome>;
      break;
    case 'klaar':
      body = (
        <div className="stack">
          <p style={{ margin: 0 }}>{text('baseConfigured')}</p>
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
        <nav aria-label={text('wizardNav')} className="wizard-steps">
          <ol>
            {INSTALL_STEPS.map((s, i) => {
              const done = progress.done.includes(s.key);
              return (
                <li key={s.key}>
                  <button type="button" className={`wizard-step${s.key === step.key ? ' current' : ''}${done ? ' done' : ''}`} aria-current={s.key === step.key ? 'step' : undefined} onClick={() => goto(s.key)}>
                    <span className="wizard-num" aria-hidden="true">{done ? <CheckIcon width={14} height={14} /> : i + 1}</span>
                    <span>{s.title}{s.key === 'gree' && <span className="small muted"> · {text('waiting')}</span>}{done && <span className="sr-only"> (gedaan)</span>}</span>
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
              <Button variant="primair" onClick={next}>{step.key === 'gree' ? text('greeLater') :
                'optional' in step && step.optional ? 'Volgende (of overslaan)' : 'Volgende'}</Button>
            )}
          </div>
        </section>
      </div>
    </>
  );
}
