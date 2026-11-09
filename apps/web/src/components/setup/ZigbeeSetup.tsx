import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { qk } from '../../api/keys';
import { ActionButton, ErrorNote, Loading, StateLabel } from '../bits';
import { KIND_LABEL, t } from '../../i18n';
import { ProviderInstructions } from './ProviderPair';

function mmss(s: number): string {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** Zigbee: register the internal Zigbee2MQTT bridge, then open a pairing window and show new sensors. */
export function ZigbeeSetup({ homeId }: { homeId: string }) {
  const qc = useQueryClient();
  const integrations = useQuery({ queryKey: qk.integrations, queryFn: () => api.integrations() });
  const zigbee = (Array.isArray(integrations.data) ? integrations.data : []).find((i) => i.provider === 'zigbee' && i.id && (!i.home_id || i.home_id === homeId));
  const [openUntil, setOpenUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const open = openUntil !== null && openUntil > now;
  const devices = useQuery({ queryKey: qk.devices(homeId), queryFn: () => api.devices(homeId), refetchInterval: open ? 5000 : false });
  const known = useRef<Set<string> | null>(null);
  const list = Array.isArray(devices.data) ? devices.data : [];
  if (known.current === null && devices.data) known.current = new Set(list.map((d) => d.id));

  const setup = useMutation({
    mutationFn: () => api.zigbeeSetup(homeId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.integrations });
      qc.invalidateQueries({ queryKey: qk.system });
    },
  });
  const join = useMutation({
    mutationFn: () => api.zigbeePermitJoin(homeId, 120),
    onSuccess: (r) => {
      known.current = new Set(list.map((d) => d.id));
      setOpenUntil(new Date(r.open_until).getTime());
    },
  });
  useEffect(() => {
    if (!open) return;
    const tm = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tm);
  }, [open]);
  const fresh = useMemo(() => list.filter((d) => d.provider === 'zigbee' && d.kind !== 'zigbee_bridge' && known.current && !known.current.has(d.id)), [list]);

  if (integrations.isLoading) return <Loading />;
  return (
    <div className="stack">
      <ProviderInstructions provider="zigbee" />
      {!zigbee ? (
        <div className="stack tight">
          <p style={{ margin: 0 }}>Zigbee is nog niet ingesteld. De app gebruikt de eigen Zigbee2MQTT-container met de USB-dongle.</p>
          <div><ActionButton className="btn primary" disabled={setup.isPending} onClick={() => setup.mutate()}>Zigbee instellen</ActionButton></div>
          {setup.error && <ErrorNote error={setup.error} />}
        </div>
      ) : (
        <div className="stack tight">
          <p style={{ margin: 0 }}><StateLabel state={zigbee.state}>{zigbee.detail || 'Zigbee ingesteld'}</StateLabel></p>
          <div className="row">
            <ActionButton className="btn primary" disabled={join.isPending || open} onClick={() => join.mutate()}>Koppelen starten (2 min)</ActionButton>
            {open && <span className="pill cool" role="timer" aria-live="off" data-testid="zigbee-countdown">Koppelvenster open, nog {mmss(Math.max(0, Math.round((openUntil! - now) / 1000)))}</span>}
          </div>
          {open && <p className="small muted" style={{ margin: 0 }} role="status">Houd nu de koppelknop van de sensor ingedrukt tot het lampje knippert. Nieuwe sensoren verschijnen hieronder.</p>}
          {join.error && <ErrorNote error={join.error} />}
        </div>
      )}
      {fresh.length > 0 && (
        <ul className="meter-list" aria-label="Nieuw gevonden sensoren">
          {fresh.map((d) => (
            <li key={d.id}><span><strong>{d.name}</strong><span className="small muted"> · {t(KIND_LABEL, d.kind)}{d.model ? ` · ${d.model}` : ''}</span></span><span className="pill ok">Nieuw</span></li>
          ))}
        </ul>
      )}
    </div>
  );
}
