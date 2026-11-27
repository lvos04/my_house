import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api } from '../../api/client';
import { ActionButton, ErrorNote } from '../../components/bits';

export function ZigbeeHerstelkopie() {
  const [bestanden, setBestanden] = useState<File[]>([]);
  const kopie = useMutation({ mutationFn: () => api.restoreZigbeeFiles(bestanden) });
  return (
    <details className="stack">
      <summary>Losse Zigbee2MQTT-bestanden herstellen</summary>
      <form className="stack" onSubmit={e => { e.preventDefault(); if (bestanden.length) kopie.mutate(); }}>
        <p>De app bewaart de gekozen bestanden samen in een besloten herstelmap. De bestaande Zigbee-opslag blijft in gebruik.</p>
        <label className="field">Zigbee2MQTT-bestanden
          <input type="file" multiple aria-required="true" disabled={kopie.isPending} onChange={e => {
            kopie.reset();
            setBestanden(Array.from(e.target.files ?? []));
          }} />
        </label>
        <p className="small muted">Kies configuration.yaml, database.db, coordinator_backup.json, state.json,
          devices.yaml, groups.yaml of het bijbehorende secret.yaml of secret.yml.</p>
        <div><ActionButton type="submit" className="btn" disabled={!bestanden.length || kopie.isPending}>
          {kopie.isPending ? 'Kopie wordt gemaakt…' : 'Herstelkopie maken'}
        </ActionButton></div>
        {kopie.error && <ErrorNote error={kopie.error} />}
        {kopie.data && <div className="note info stack" role="status">
          <strong>Herstelkopie beschikbaar</strong>
          <span>De herstelmap onder de appopslag is <code style={{ overflowWrap: 'anywhere' }}>{kopie.data.map}</code>.</span>
          <ul>{kopie.data.bestanden.map(naam => <li key={naam}>{naam}</li>)}</ul>
          <span>Vergelijk deze bestanden op een afzonderlijke, gestopte installatie voordat je de kopie in gebruik neemt.</span>
        </div>}
      </form>
    </details>
  );
}
