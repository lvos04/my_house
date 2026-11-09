// Outdoor temperature role picker (verbeterlijst 45): GET /homes/{id}/climate/roles shows the bound
// device; binding uses POST /devices/{id}/bind {role:"outdoor_temp"} (only Zigbee sensors fit).
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import { ActionButton, Loading } from '../components/bits';
import { LinkedIcon, UnlinkedIcon } from '../components/Icons';
import { FormError, SafeLink, useClimateCan } from './common';

export const OUTDOOR_ROLE = 'outdoor_temp';

export function OutdoorSensorCard({ homeId }: { homeId: string }) {
  const qc = useQueryClient();
  const { admin } = useClimateCan();
  const roles = useQuery({ queryKey: qk.climateRoles(homeId), queryFn: () => api.climateRoles(homeId) });
  const devices = useQuery({ queryKey: qk.devices(homeId), queryFn: () => api.devices(homeId), enabled: admin });
  const role = Array.isArray(roles.data) ? roles.data.find((r) => r.role === OUTDOOR_ROLE) : undefined;
  const candidates = (Array.isArray(devices.data) ? devices.data : []).filter((d) => (d.allowed_roles ?? []).includes(OUTDOOR_ROLE) && d.lifecycle !== 'archived');
  const [pick, setPick] = useState('');
  useEffect(() => setPick(role?.device_id ?? ''), [role?.device_id]);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: qk.climateRoles(homeId) });
    qc.invalidateQueries({ queryKey: qk.devices(homeId) });
    qc.invalidateQueries({ queryKey: ['experiment'] });
  };
  const bind = useMutation({ mutationFn: (id: string) => api.bindDevice(id, OUTDOOR_ROLE), onSuccess: refresh });
  const unbind = useMutation({ mutationFn: (id: string) => api.unbindDevice(id, OUTDOOR_ROLE), onSuccess: refresh });
  const label = role?.label ?? 'Buitentemperatuur';

  return (
    <section className="card" aria-labelledby="outdoor-h">
      <div className="section-head" style={{ alignItems: 'center' }}>
        <h3 id="outdoor-h">{label}</h3>
        {role && <span className={`pill ${role.device_id ? 'ok' : 'neutral'}`}>{role.device_id ? <LinkedIcon /> : <UnlinkedIcon />}{role.device_id ? 'Gekoppeld' : 'Niet gekoppeld'}</span>}
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        Met een buitensensor weegt een experiment het weer mee. Zonder buitensensor is de uitkomst altijd indicatief.
      </p>
      {roles.isLoading ? <Loading /> : role?.device_id ? (
        <p style={{ margin: 0 }}>Buitensensor: <strong>{role.device_name ?? 'onbekend apparaat'}</strong></p>
      ) : (
        <p style={{ margin: 0 }}>Nog geen buitensensor gekoppeld.</p>
      )}
      {admin && (
        devices.isLoading ? <Loading /> : candidates.length ? (
          <div className="form-grid">
            <label className="field">Buitensensor kiezen
              <select value={pick} onChange={(e) => setPick(e.target.value)}>
                <option value="">Kies een sensor…</option>
                {candidates.map((d) => <option key={d.id} value={d.id}>{d.name}{d.room ? ` (${d.room})` : ''}</option>)}
              </select>
            </label>
            <ActionButton className="btn primary" disabled={!pick || pick === role?.device_id || bind.isPending} onClick={() => bind.mutate(pick)}>
              Als buitensensor gebruiken
            </ActionButton>
            {role?.device_id && (
              <ActionButton
                className="btn danger"
                disabled={unbind.isPending}
                onClick={() => {
                  if (window.confirm(`${role.device_name ?? 'Deze sensor'} niet meer als buitensensor gebruiken? De metingen tot nu blijven bewaard.`)) unbind.mutate(role.device_id!);
                }}
              >
                Loskoppelen
              </ActionButton>
            )}
          </div>
        ) : (
          <p className="small muted" style={{ margin: 0 }}>
            Er is nog geen Zigbee-sensor die buiten kan hangen. Koppel er een via <SafeLink to="/apparaten">Apparaten</SafeLink> en kies hem dan hier.
          </p>
        )
      )}
      {bind.isSuccess && <span className="small muted" role="status">Buitensensor ingesteld.</span>}
      <FormError error={roles.error ?? bind.error ?? unbind.error} />
    </section>
  );
}
