import { useState, type FormEvent, type ReactNode } from 'react';
import { Link, useInRouterContext } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { Compatibility, Device, Room } from '../api/types';
import { ActionButton, ErrorNote, StateLabel, yesNo } from '../components/bits';
import { ago, temp } from '../format';
import { COMPATIBILITY_LABEL, LIFECYCLE_LABEL, MODE_LABEL, PROVIDER_LABEL, t } from '../i18n';
import { RoleControl } from './RoleControl';
import { RelayStatus } from './RelayStatus';
import { CalibrationForm, CALIBRATABLE } from './Calibration';
import { ArchiveDevice, DeviceTestButton, DiagnosticsBlock, MarkTested, ReplaceDevice } from './DeviceTools';
import { AVAILABILITY_LABEL, calibrationText, kindLabel, POWER_SOURCE_LABEL } from './labels';

export const PROTECTED_TEXT = 'Alleen meten — schakelen geblokkeerd. Let op: de HomeWizard-app kan de stekker nog wel schakelen.';

/** Router link when inside a router, else a plain link (the page is also rendered without one). */
export function SafeLink({ to, children }: { to: string; children: ReactNode }) {
  return useInRouterContext() ? <Link to={to}>{children}</Link> : <a href={to}>{children}</a>;
}

function LockIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 018 0v4" />
    </svg>
  );
}

export function CompatBadge({ c }: { c: Compatibility }) {
  const cls = c === 'tested' ? 'ok' : c === 'unsupported' ? 'error' : 'neutral';
  return <span className={`pill ${cls} dv-compat`}>{t(COMPATIBILITY_LABEL, c)}</span>;
}

function Facts({ items }: { items: (string | null | false | undefined)[] }) {
  const list = items.filter(Boolean) as string[];
  if (!list.length) return null;
  return <ul className="plain-list small dv-facts">{list.map((x) => <li key={x}>{x}</li>)}</ul>;
}

function zigbeeFacts(d: Device): string[] {
  const z = d.details?.zigbee;
  if (!z) return [];
  const out: string[] = [];
  if (z.friendly_name && z.friendly_name !== d.name) out.push(`Naam in Zigbee2MQTT: ${z.friendly_name}`);
  if (z.interview_completed === false) out.push('Aanmelden nog bezig (Zigbee2MQTT vraagt het apparaat uit)');
  if (z.supported === false) out.push('Niet ondersteund door Zigbee2MQTT');
  if (z.power_source) out.push(`Voeding: ${POWER_SOURCE_LABEL[z.power_source] ?? z.power_source}`);
  if (z.availability) out.push(`Zigbee2MQTT meldt: ${AVAILABILITY_LABEL[z.availability] ?? z.availability}`);
  if (d.kind === 'zigbee_bridge') {
    if (z.mqtt_connected !== undefined) out.push(`MQTT verbonden: ${yesNo(z.mqtt_connected)}`);
    if (z.bridge_state) out.push(`Zigbee2MQTT: ${z.bridge_state === 'online' ? 'online' : z.bridge_state === 'offline' ? 'offline' : z.bridge_state}`);
    if (z.zigbee2mqtt_version) out.push(`Versie Zigbee2MQTT: ${z.zigbee2mqtt_version}`);
  }
  return out;
}

function unitFacts(d: Device): string[] {
  const u = d.details?.unit;
  if (!u) return [];
  const parts: string[] = [];
  if (u.mode) parts.push(`modus ${t(MODE_LABEL, u.mode).toLowerCase()}`);
  if (u.target_c !== null && u.target_c !== undefined) parts.push(`doel ${temp(u.target_c, 0)}`);
  if (u.indoor_c !== null && u.indoor_c !== undefined && u.indoor_sensor !== false) parts.push(`binnen ${temp(u.indoor_c)}`);
  return parts.length ? [`Unit meldt: ${parts.join(', ')}`] : [];
}

function NameRoomForm({ device, rooms, homeId }: { device: Device; rooms: Room[]; homeId: string }) {
  const qc = useQueryClient();
  const [name, setName] = useState(device.name);
  const [room, setRoom] = useState(device.room_id ?? '');
  const save = useMutation({
    mutationFn: () => api.patchDevice(device.id, { ...(name.trim() && name.trim() !== device.name ? { name: name.trim() } : {}), ...(room !== (device.room_id ?? '') ? { room_id: room || null } : {}) }),
    onSuccess: (d) => {
      qc.setQueryData<Device[]>(qk.devices(homeId), (l) => l?.map((x) => (x.id === d.id ? d : x)));
      qc.invalidateQueries({ queryKey: qk.devices(homeId) });
    },
  });
  const changed = (name.trim() && name.trim() !== device.name) || room !== (device.room_id ?? '');
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (changed) save.mutate();
  };
  return (
    <form className="form-grid" onSubmit={submit} aria-label={`Naam en ruimte van ${device.name}`}>
      <label className="field">Naam<input value={name} maxLength={100} required onChange={(e) => setName(e.target.value)} /></label>
      <label className="field">
        Ruimte
        <select value={room} onChange={(e) => setRoom(e.target.value)}>
          <option value="">Geen ruimte</option>
          {rooms.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>
      </label>
      <ActionButton type="submit" className="btn" disabled={!changed || save.isPending}>Opslaan</ActionButton>
      {save.error && <ErrorNote error={save.error} />}
    </form>
  );
}

/** One device: what it is, its state and quality, roles, protection and relay state; admins get the
 * management panel (name/room, calibration, test, tested, diagnostics, replace, archive). */
export function DeviceCard({ d, all, homeId, admin, rooms, onNotice }: { d: Device; all: Device[]; homeId: string; admin: boolean; rooms: Room[]; onNotice: (s: string) => void }) {
  const [open, setOpen] = useState(false);
  const panelId = `dv-panel-${d.id}`;
  const cal = calibrationText(d);
  const relay = d.details?.relay;
  const sub = [kindLabel(d.kind), d.model, t(PROVIDER_LABEL, d.provider), d.room].filter(Boolean).join(' · ');
  return (
    <article className={`dv-card${d.online ? '' : ' is-offline'}`} aria-labelledby={`dv-${d.id}`} data-testid={`device-${d.id}`}>
      <div className="dv-head">
        <div className="dv-title">
          <h3 id={`dv-${d.id}`}>{d.name}</h3>
          <span className="small muted">{sub}</span>
        </div>
        <div className="dv-badges">
          {d.lifecycle !== 'active' && <span className="pill neutral">{t(LIFECYCLE_LABEL, d.lifecycle)}</span>}
          <span className="small"><StateLabel state={d.online ? 'ok' : 'error'}>{d.online ? 'Online' : `Offline · ${ago(d.last_seen)}`}</StateLabel></span>
          <CompatBadge c={d.compatibility} />
        </div>
      </div>
      <Facts
        items={[
          ...zigbeeFacts(d),
          d.kind === 'zigbee_device' && 'Type nog onbekend: wordt een sensor zodra Zigbee2MQTT het type doorgeeft.',
          ...unitFacts(d),
          cal,
          d.kind === 'button' && d.details?.buttons !== undefined && `${d.details.buttons} ${d.details.buttons === 1 ? 'knop' : 'knoppen'}${d.details.bridge_rules ? `, ${d.details.bridge_rules} met een regel op de Hue Bridge` : ''}`,
        ]}
      />
      {d.kind === 'button' && <span className="small"><SafeLink to="/verlichting">Acties van deze knop instellen bij Verlichting</SafeLink></span>}
      <RoleControl device={d} all={all} homeId={homeId} admin={admin} />
      {d.protected && (
        <div className="small dv-protected">
          <span aria-hidden="true"><LockIcon /></span><span>{PROTECTED_TEXT}</span>
        </div>
      )}
      {relay && <RelayStatus relay={relay} protectedDevice={d.protected} />}
      {admin && (
        <div>
          <button type="button" className="btn small" aria-expanded={open} aria-controls={panelId} aria-label={`${open ? 'Beheren sluiten' : 'Beheren'}: ${d.name}`} onClick={() => setOpen(!open)}>
            {open ? 'Beheren sluiten' : 'Beheren'}
          </button>
        </div>
      )}
      {admin && open && (
        <div id={panelId} className="dv-panel stack">
          <section className="stack tight"><h4>Naam en ruimte</h4><NameRoomForm device={d} rooms={rooms} homeId={homeId} /></section>
          {CALIBRATABLE.has(d.kind) && <CalibrationForm device={d} homeId={homeId} />}
          <section className="stack tight"><h4>Testen</h4><DeviceTestButton device={d} /><MarkTested device={d} homeId={homeId} /></section>
          <section className="stack tight"><h4>Diagnose</h4><DiagnosticsBlock device={d} /></section>
          <section className="stack tight">
            <h4>Vervangen of archiveren</h4>
            <ReplaceDevice device={d} all={all} homeId={homeId} onNotice={onNotice} />
            <ArchiveDevice device={d} homeId={homeId} onNotice={onNotice} />
          </section>
        </div>
      )}
    </article>
  );
}
