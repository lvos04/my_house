import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { VerifiedDatabaseDump } from '../../api/types';
import { ActionButton, ErrorNote } from '../../components/bits';

interface Props {
  dump: VerifiedDatabaseDump;
  passphrase: string;
  onDone: (database: string) => void;
  onBack: () => void;
}

export function DatabaseDumpHerstel({ dump, passphrase, onDone, onBack }: Props) {
  const [naam, setNaam] = useState('');
  const geldig = /^[a-z][a-z0-9_]{0,62}$/.test(naam);
  const upload = dump.dump_upload_id;
  const herstel = useMutation({
    mutationFn: () => api.restoreDump(upload!, passphrase, naam),
    onSuccess: result => onDone(result.database),
  });
  const sluit = useMutation({
    mutationFn: async () => { if (upload) await api.restoreDiscard(upload); },
    onSuccess: onBack,
  });
  const bezig = herstel.isPending || sluit.isPending;
  return (
    <section className="stack" aria-labelledby="dump-kop">
      <h3 id="dump-kop">Database-dump gecontroleerd</h3>
      <p>De app heeft de versleutelde dump volledig gelezen en het connectorherstelbewijs gecontroleerd.</p>
      {upload ? <form className="stack" onSubmit={e => { e.preventDefault(); if (geldig) herstel.mutate(); }}>
        <p>De installatiesleutel bevestigt de herkomst. Kies een vrije databasenaam. De bestaande woning blijft beschikbaar.</p>
        <label className="field">Naam van de nieuwe database
          <input value={naam} onChange={e => setNaam(e.target.value)} disabled={bezig} maxLength={63}
            aria-describedby="dump-naam-hulp" required />
        </label>
        <span className="small muted" id="dump-naam-hulp">Begin met een kleine letter. Gebruik kleine letters, cijfers en underscores.</span>
        <div><ActionButton type="submit" className="btn primary" disabled={!geldig || bezig}>
          {herstel.isPending ? 'Database wordt hersteld…' : 'In nieuwe database herstellen'}
        </ActionButton></div>
      </form> : <p>Voor deze dump ontbreekt een herkomstbewijs van de huidige installatiesleutel.
        Gebruik voor een dump uit je eigen back-up <code>shc-api restore-dump</code> met een nieuwe databasenaam.</p>}
      {herstel.error && <ErrorNote error={herstel.error} />}
      {sluit.error && <ErrorNote error={sluit.error} />}
      <div><ActionButton className="btn" disabled={bezig} onClick={() => sluit.mutate()}>Ander bestand kiezen</ActionButton></div>
    </section>
  );
}
