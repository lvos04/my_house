import { useState, type FormEvent } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { Device } from '../api/types';
import { ActionButton, ErrorNote } from '../components/bits';
import { roleLabel } from './labels';

/** Roles the picker offers: only those that fit the device kind (allowed_roles), minus the ones it has. */
export function offeredRoles(d: Device): string[] {
  return (d.allowed_roles ?? []).filter((r) => !d.roles.includes(r));
}

/** What binding `role` to `d` does, and whether to ask first. Asking is needed when it re-binds:
 * another device holds the role (it is then disconnected and marked "vervangen"), the device already
 * has a role, or it is a protected (boiler) device. */
export function bindPlan(d: Device, role: string, all: Device[]): { confirm: boolean; message: string } {
  const holder = all.find((x) => x.id !== d.id && x.lifecycle !== 'archived' && x.roles.includes(role));
  const parts = [`${d.name} koppelen als ‘${roleLabel(role)}’?`];
  if (holder) parts.push(`${holder.name} heeft deze rol nu. Die wordt losgekoppeld en als ‘vervangen’ gemarkeerd; de geschiedenis van ${holder.name} blijft bewaard.`);
  if (d.roles.length) parts.push(`${d.name} houdt ook ${d.roles.map((r) => `‘${roleLabel(r)}’`).join(' en ')}.`);
  if (d.protected) parts.push('Dit apparaat is beschermd: het blijft alleen-meten en wordt nooit geschakeld.');
  return { confirm: Boolean(holder || d.roles.length || d.protected), message: parts.join(' ') };
}

export function unbindMessage(d: Device, role: string): string {
  const parts = [`Rol ‘${roleLabel(role)}’ loskoppelen van ${d.name}? Vanaf nu telt ${d.name} hier niet meer voor mee; de geschiedenis blijft bewaard.`];
  if (role === 'boiler') parts.push('Let op: de boilerstekker wordt dan niet meer bewaakt (geen melding als hij uitgaat).');
  return parts.join(' ');
}

/** Roles of a device: chips with "loskoppelen", and a picker limited to roles that fit (item 24, kleine punten). */
export function RoleControl({ device, all, homeId, admin }: { device: Device; all: Device[]; homeId: string; admin: boolean }) {
  const qc = useQueryClient();
  const [pick, setPick] = useState('');
  const done = () => {
    qc.invalidateQueries({ queryKey: qk.devices(homeId) });
    setPick('');
  };
  const bind = useMutation({ mutationFn: (role: string) => api.bindDevice(device.id, role), onSuccess: done });
  const unbind = useMutation({ mutationFn: (role: string) => api.unbindDevice(device.id, role), onSuccess: done });
  const offered = offeredRoles(device);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!pick) return;
    const plan = bindPlan(device, pick, all);
    if (!plan.confirm || window.confirm(plan.message)) bind.mutate(pick);
  };
  if (!admin) {
    return device.roles.length ? (
      <div className="row dv-roles">{device.roles.map((r) => <span key={r} className="pill neutral">{roleLabel(r)}</span>)}</div>
    ) : null;
  }
  if (!device.roles.length && !offered.length) return null;
  return (
    <div className="stack tight dv-roles">
      {device.roles.length > 0 && (
        <div className="row" style={{ gap: 6 }}>
          {device.roles.map((r) => (
            <span key={r} className="pill neutral dv-role">
              {roleLabel(r)}
              <ActionButton
                className="btn small ghost dv-unbind"
                aria-label={`Rol ${roleLabel(r)} loskoppelen van ${device.name}`}
                disabled={unbind.isPending}
                onClick={() => {
                  if (window.confirm(unbindMessage(device, r))) unbind.mutate(r);
                }}
              >
                Loskoppelen
              </ActionButton>
            </span>
          ))}
        </div>
      )}
      {offered.length > 0 && (
        <form className="dv-role-form" onSubmit={submit}>
          <span aria-hidden="true" className="small muted dv-role-cap">{device.roles.length ? 'Nog een rol' : 'Rol'}</span>
          <label className="field">
            <span className="sr-only">Rol van {device.name}</span>
            <select value={pick} onChange={(e) => setPick(e.target.value)}>
              <option value="">Kies een rol…</option>
              {offered.map((r) => {
                const holder = all.find((x) => x.id !== device.id && x.lifecycle !== 'archived' && x.roles.includes(r));
                return <option key={r} value={r}>{roleLabel(r)}{holder ? ` (nu: ${holder.name})` : ''}</option>;
              })}
            </select>
          </label>
          <ActionButton type="submit" className="btn small" aria-label={`Rol koppelen: ${device.name}`} disabled={!pick || bind.isPending}>Koppelen</ActionButton>
        </form>
      )}
      {(bind.error || unbind.error) && <ErrorNote error={bind.error ?? unbind.error} />}
    </div>
  );
}
