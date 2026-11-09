import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { qk } from '../../api/keys';
import type { Device, Zone } from '../../api/types';
import { ActionButton, Empty, ErrorNote, Loading } from '../bits';
import { KIND_LABEL, PROVIDER_LABEL, ROLE_LABEL, t } from '../../i18n';

function useDevices(homeId: string) {
  const q = useQuery({ queryKey: qk.devices(homeId), queryFn: () => api.devices(homeId) });
  return { ...q, list: Array.isArray(q.data) ? q.data : [] };
}

function ZoneRow({ zone, units, sensors, homeId }: { zone: Zone; units: Device[]; sensors: Device[]; homeId: string }) {
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (body: { unit_device_id?: string | null; sensor_device_id?: string | null }) => api.patchZone(zone.id, body),
    onSuccess: (z) => {
      qc.setQueryData<Zone[]>(qk.zones(homeId), (l) => l?.map((x) => (x.id === z.id ? z : x)));
      qc.invalidateQueries({ queryKey: qk.overview(homeId) });
    },
  });
  return (
    <li className="assign-row">
      <strong>{zone.name}</strong>
      <div className="form-grid">
        <label className="field">
          Airco-unit
          <select aria-label={`Airco-unit voor ${zone.name}`} value={zone.unit_device_id ?? ''} disabled={save.isPending} onChange={(e) => save.mutate({ unit_device_id: e.target.value || null })}>
            <option value="">Geen</option>
            {units.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </label>
        <label className="field">
          Temperatuursensor
          <select aria-label={`Temperatuursensor voor ${zone.name}`} value={zone.sensor_device_id ?? ''} disabled={save.isPending} onChange={(e) => save.mutate({ sensor_device_id: e.target.value || null })}>
            <option value="">Geen (sensor van de airco)</option>
            {sensors.map((d) => <option key={d.id} value={d.id}>{d.name}{d.room ? ` (${d.room})` : ''}</option>)}
          </select>
        </label>
      </div>
      {save.isSuccess && <span className="small muted" role="status">Opgeslagen.</span>}
      {save.error && <ErrorNote error={save.error} />}
    </li>
  );
}

/** Link each climate zone to its airco unit and (optional) external temperature sensor. */
export function SensorAssign({ homeId }: { homeId: string }) {
  const zones = useQuery({ queryKey: qk.zones(homeId), queryFn: () => api.zones(homeId) });
  const devices = useDevices(homeId);
  const units = devices.list.filter((d) => d.kind === 'ac_unit');
  const sensors = devices.list.filter((d) => d.kind === 'zigbee_sensor');
  const zl = Array.isArray(zones.data) ? zones.data : [];
  if (zones.isLoading || devices.isLoading) return <Loading />;
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>
        Met een eigen sensor in de kamer regelt de app op de echte kamertemperatuur; zonder sensor gebruikt de airco zijn eigen (minder nauwkeurige) sensor.
      </p>
      {(zones.error || devices.error) && <ErrorNote error={zones.error ?? devices.error} />}
      {zl.length === 0 ? <Empty>Geen klimaatzones.</Empty> : (
        <ul className="plain-list stack">{zl.map((z) => <ZoneRow key={z.id} zone={z} units={units} sensors={sensors} homeId={homeId} />)}</ul>
      )}
      {units.length === 0 && <p className="small muted" style={{ margin: 0 }}>Nog geen airco-units gekoppeld (stap Gree).</p>}
      {sensors.length === 0 && <p className="small muted" style={{ margin: 0 }}>Nog geen Zigbee-sensoren gekoppeld (stap Zigbee).</p>}
    </div>
  );
}

function RoleRow({ device, homeId }: { device: Device; homeId: string }) {
  const qc = useQueryClient();
  const [pick, setPick] = useState('');
  const done = (d: Device) => {
    qc.setQueryData<Device[]>(qk.devices(homeId), (l) => l?.map((x) => (x.id === d.id ? d : x)));
    qc.invalidateQueries({ queryKey: qk.devices(homeId) });
    setPick('');
  };
  const bind = useMutation({ mutationFn: (role: string) => api.bindDevice(device.id, role), onSuccess: done });
  const unbind = useMutation({ mutationFn: (role: string) => api.unbindDevice(device.id, role), onSuccess: done });
  const free = device.allowed_roles.filter((r) => !device.roles.includes(r));
  return (
    <li className="assign-row">
      <span><strong>{device.name}</strong><span className="small muted"> · {t(KIND_LABEL, device.kind)} · {t(PROVIDER_LABEL, device.provider)}</span></span>
      <div className="row">
        {device.roles.length === 0 && <span className="small muted">Geen rol</span>}
        {device.roles.map((r) => (
          <span key={r} className="pill neutral">
            {t(ROLE_LABEL, r)}
            <button
              type="button"
              className="chip-x"
              aria-label={`Rol ${t(ROLE_LABEL, r)} loskoppelen van ${device.name}`}
              disabled={unbind.isPending}
              onClick={() => { if (window.confirm(`Rol ‘${t(ROLE_LABEL, r)}’ loskoppelen van ${device.name}? De geschiedenis blijft.`)) unbind.mutate(r); }}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      {free.length > 0 && (
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (pick && window.confirm(`${device.name} koppelen als ‘${t(ROLE_LABEL, pick)}’? Een ander apparaat met deze rol wordt dan losgekoppeld.`)) bind.mutate(pick); }}>
          <label className="field" style={{ flex: 1 }}>
            Rol toevoegen
            <select aria-label={`Rol voor ${device.name}`} value={pick} onChange={(e) => setPick(e.target.value)}>
              <option value="">Kies…</option>
              {free.map((r) => <option key={r} value={r}>{t(ROLE_LABEL, r)}</option>)}
            </select>
          </label>
          <ActionButton type="submit" className="btn" disabled={!pick || bind.isPending}>Koppelen</ActionButton>
        </form>
      )}
      {device.protected && <span className="small muted">Beschermd: deze stekker wordt alleen gemeten en nooit geschakeld.</span>}
      {(bind.error || unbind.error) && <ErrorNote error={bind.error ?? unbind.error} />}
    </li>
  );
}

/** Bind meters and sensors to their roles (P1 = huis totaal, kWh-meter = airco, boiler-stekker, buitensensor). */
export function RoleAssign({ homeId }: { homeId: string }) {
  const devices = useDevices(homeId);
  const candidates = devices.list.filter((d) => d.allowed_roles?.length > 0 && d.lifecycle !== 'archived');
  if (devices.isLoading) return <Loading />;
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>
        Alleen rollen die bij het soort apparaat passen worden aangeboden. De boilerstekker wordt alleen gemeten; de app schakelt hem nooit.
      </p>
      {devices.error && <ErrorNote error={devices.error} />}
      {candidates.length === 0 ? <Empty>Nog geen meters of sensoren die een rol kunnen krijgen.</Empty> : (
        <ul className="plain-list stack">{candidates.map((d) => <RoleRow key={d.id} device={d} homeId={homeId} />)}</ul>
      )}
    </div>
  );
}
