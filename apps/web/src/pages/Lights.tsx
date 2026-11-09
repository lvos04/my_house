import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { HueButton, Lights, Zone } from '../api/types';
import { useHome } from '../state';
import { useCan } from '../user';
import { Empty, ErrorNote, Loading, PaginaFout } from '../components/bits';
import { UnknownIcon } from '../components/Icons';
import { GroupCard } from '../lights/GroupCard';
import { ButtonCard } from '../lights/ButtonEditor';
import { buildNames } from '../lights/mapping';

function LampList({ lights }: { lights: Lights }) {
  const lamps = lights.lights ?? [];
  if (!lamps.length) return null;
  const off = lamps.filter((l) => !l.reachable);
  return (
    <details className="card lt-lamps">
      <summary><h2 className="lt-h2" style={{ display: 'inline' }}>Lampen ({lamps.length}{off.length ? `, ${off.length} niet bereikbaar` : ''})</h2></summary>
      <ul className="meter-list">
        {lamps.map((l) => (
          <li key={l.id} className={l.reachable ? undefined : 'lt-lamp-off'}>
            <span>{l.name}{l.model ? <span className="small muted"> · {l.model}</span> : null}</span>
            <span className="row" style={{ gap: 8 }}>
              {!l.reachable ? (
                <span className="pill unknown"><UnknownIcon />Niet bereikbaar</span>
              ) : (
                <span className={`pill ${l.on ? 'light' : 'neutral'}`}>{l.on ? `Aan${l.brightness !== null ? ` · ${l.brightness}%` : ''}` : 'Uit'}</span>
              )}
            </span>
            {l.last_command && (l.last_command.status === 'failed' || l.last_command.status === 'denied') && (
              <span className="small error-text" style={{ width: '100%' }}>Laatste opdracht mislukt{l.last_command.detail ? `: ${l.last_command.detail}` : ''}</span>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function LightsPage() {
  const { homeId } = useHome();
  const can = useCan();
  const lights = useQuery({ queryKey: qk.lights(homeId ?? ''), queryFn: () => api.lights(homeId!), enabled: !!homeId, refetchInterval: 60_000 });
  const buttons = useQuery({ queryKey: qk.buttons(homeId ?? ''), queryFn: () => api.buttons(homeId!), enabled: !!homeId });
  const zones = useQuery({ queryKey: qk.zones(homeId ?? ''), queryFn: () => api.zones(homeId!), enabled: !!homeId });
  if (!homeId || lights.isLoading) return <Loading titel="Verlichting" />;
  const data: Lights | null = lights.data && typeof lights.data === 'object' && !Array.isArray(lights.data) ? lights.data : null;
  if (!data) return <PaginaFout titel="Verlichting" error={lights.error ?? new Error('Verlichting kon niet worden geladen.')} />;
  const zoneList: Zone[] = Array.isArray(zones.data) ? zones.data : [];
  const buttonList: HueButton[] = Array.isArray(buttons.data) ? buttons.data : [];
  const rooms = data.groups.filter((g) => !g.all_lights && !g.hidden);
  const whole = data.groups.filter((g) => g.all_lights || g.hidden);
  const scenesOf = (id: string) => data.scenes.filter((s) => s.group_id === id);
  const unreachable = (data.lights ?? []).filter((l) => !l.reachable);
  const names = buildNames(data, zoneList);

  return (
    <>
      <header className="page-head">
        <div><span className="eyebrow">Philips Hue</span><h1>Verlichting</h1></div>
      </header>
      {unreachable.length > 0 && (
        // Een lamp die niet antwoordt is Onbekend (stippel), geen Beperkt (Peil §4.1 regel 4).
        <p className="note unknown" style={{ margin: 0 }}>
          Niet bereikbaar: {unreachable.map((l) => l.name).join(', ')}. Staat de lichtschakelaar uit, of is de lamp buiten bereik van de bridge?
        </p>
      )}
      {rooms.length ? (
        <div className="grid-auto">
          {rooms.map((g) => <GroupCard key={g.id} group={g} scenes={scenesOf(g.id)} control={can.control} />)}
        </div>
      ) : (
        <Empty>Geen Hue-kamers gevonden. Koppel de Hue Bridge via Apparaten.</Empty>
      )}
      {whole.length > 0 && (
        <section aria-labelledby="whole-h" className="section">
          <h2 id="whole-h" className="lt-h2">Hele huis</h2>
          <div className="grid-auto">
            {whole.map((g) => <GroupCard key={g.id} group={g} scenes={scenesOf(g.id)} control={can.control} />)}
          </div>
        </section>
      )}
      <LampList lights={data} />
      <section aria-labelledby="knop-h" className="section">
        <div className="section-head">
          <h2 id="knop-h">Knoppen</h2>
        </div>
        <p className="small muted" style={{ margin: 0 }}>
          Wat de Hue Bridge zelf met een knop doet, blijft werken, ook als deze app uit staat. Hier koppel je er extra acties aan: licht, klimaat, of bewust allebei.
          {can.admin ? ' Met ‘Simuleer’ zie je wat een druk nu zou doen, zonder iets uit te voeren.' : ''}
        </p>
        {buttons.isLoading && <Loading />}
        {buttons.error && <ErrorNote error={buttons.error} />}
        {buttons.isSuccess && buttonList.length === 0 && <Empty>Geen Hue-knoppen gevonden.</Empty>}
        {buttonList.map((b) => <ButtonCard key={b.id} hb={b} names={names} lights={data} zones={zoneList} homeId={homeId} admin={can.admin} />)}
      </section>
    </>
  );
}
