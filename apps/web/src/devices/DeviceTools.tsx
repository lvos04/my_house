import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { Device, DeviceDiagnostics, DeviceTest, ReplaceResult } from '../api/types';
import { ActionButton, CopyButton, ErrorNote, StateLabel } from '../components/bits';
import { dateOnly, dateTime } from '../format';
import { CHECK_STATE_LABEL } from '../i18n';
import { CODE_HINT, METRIC_LABEL, roleLabel } from './labels';

function fmtValue(v: number | null, unit: string | null): string {
  if (v === null || v === undefined) return '–';
  return `${v.toLocaleString('nl-NL', { maximumFractionDigits: 2 })}${unit ? ` ${unit}` : ''}`;
}

export function TestResult({ result }: { result: DeviceTest }) {
  const readings = Object.entries(result.readings ?? {});
  return (
    <div className={`note ${result.ok ? 'ok' : 'error'} stack tight dv-test`} data-testid="device-test">
      <strong>{result.ok ? 'Test geslaagd' : 'Test vond een probleem'} · {dateTime(result.checked_at)}</strong>
      <ul className="plain-list stack tight">
        {result.checks.map((c) => (
          <li key={c.key}>
            <StateLabel state={c.state}>{c.label}: {c.detail || CHECK_STATE_LABEL[c.state]}</StateLabel>
            {c.code && CODE_HINT[c.code] && <span className="small">{CODE_HINT[c.code]}</span>}
          </li>
        ))}
      </ul>
      {readings.length > 0 && (
        <dl className="facts compact">
          {readings.map(([k, r]) => (
            <div key={k}><dt>{METRIC_LABEL[k] ?? k}</dt><dd>{fmtValue(r.value, r.unit)} · {dateTime(r.at)}</dd></div>
          ))}
        </dl>
      )}
      <span className="small">{result.note}</span>
    </div>
  );
}

/** Read-only test of one device (never switches anything). */
export function DeviceTestButton({ device }: { device: Device }) {
  const test = useMutation({ mutationFn: () => api.testDevice(device.id) });
  return (
    <div className="stack tight">
      <div className="row">
        <ActionButton className="btn" disabled={test.isPending} onClick={() => test.mutate()} aria-describedby={`t-${device.id}`}>{test.isPending ? 'Testen…' : 'Testen'}</ActionButton>
        <span id={`t-${device.id}`} className="small muted">Leest alleen uit; schakelt niets.</span>
      </div>
      {test.data && <TestResult result={test.data} />}
      {test.error && <ErrorNote error={test.error} />}
    </div>
  );
}

/** Manual "works in this home" confirmation (verbeterlijst 29). */
export function MarkTested({ device, homeId }: { device: Device; homeId: string }) {
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const mark = useMutation({
    mutationFn: () => api.markTested(device.id, note.trim() || undefined),
    onSuccess: (d) => {
      qc.setQueryData<Device[]>(qk.devices(homeId), (l) => l?.map((x) => (x.id === d.id ? d : x)));
      qc.invalidateQueries({ queryKey: qk.compatibility });
    },
  });
  if (device.compatibility === 'tested') {
    return <p className="small" style={{ margin: 0 }}>Getest in dit huis{device.tested_at ? ` op ${dateOnly(device.tested_at)}` : ''}.</p>;
  }
  return (
    <div className="stack tight">
      <label className="field">
        Opmerking bij de test (optioneel)
        <input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} placeholder="bijv. meten en schakelen werken" />
      </label>
      <div className="row">
        <ActionButton
          className="btn"
          disabled={mark.isPending}
          onClick={() => {
            if (window.confirm(`Bevestig je dat ${device.name} in dit huis goed werkt? Het apparaat krijgt dan de status ‘getest’.`)) mark.mutate();
          }}
        >
          Getest bevestigen
        </ActionButton>
        <span className="small muted">Alleen als je meten (en waar het kan bedienen) zelf hebt gecontroleerd.</span>
      </div>
      {mark.error && <ErrorNote error={mark.error} />}
    </div>
  );
}

/** Diagnostics without secrets, ready to copy for support (verbeterlijst 29). */
export function DiagnosticsBlock({ device }: { device: Device }) {
  const diag = useMutation({ mutationFn: () => api.deviceDiagnostics(device.id) });
  const data: DeviceDiagnostics | undefined = diag.data;
  return (
    <div className="stack tight">
      {!data && (
        <div>
          <ActionButton className="btn" disabled={diag.isPending} onClick={() => diag.mutate()}>{diag.isPending ? 'Laden…' : 'Diagnose tonen'}</ActionButton>
        </div>
      )}
      {data && (
        <div className="stack tight" data-testid="diagnostics">
          <p className="small muted" style={{ margin: 0 }}>Zonder wachtwoorden of sleutels. Stuur dit mee als je hulp vraagt.</p>
          <pre className="code-block" tabIndex={0} aria-label={`Diagnose ${device.name}`}>{data.text}</pre>
          <div className="row"><CopyButton text={data.text} label="Diagnose kopiëren" /></div>
        </div>
      )}
      {diag.error && <ErrorNote error={diag.error} />}
    </div>
  );
}

/** Devices that can take over this one's roles, zone links and button mappings. */
export function replacementCandidates(d: Device, all: Device[]): Device[] {
  return all.filter(
    (x) =>
      x.id !== d.id &&
      x.lifecycle !== 'archived' &&
      (x.kind === d.kind || (d.roles.length > 0 && d.roles.every((r) => x.allowed_roles?.includes(r)))),
  );
}

export function replaceSummary(r: ReplaceResult): string {
  const parts: string[] = [];
  if (r.moved.roles.length) parts.push(`rol ${r.moved.roles.map(roleLabel).join(', ')}`);
  if (r.moved.zones) parts.push(`${r.moved.zones} ${r.moved.zones === 1 ? 'zone' : 'zones'}`);
  if (r.moved.mappings) parts.push(`${r.moved.mappings} knopkoppeling${r.moved.mappings === 1 ? '' : 'en'}`);
  return `${r.old.name} is vervangen door ${r.new.name}.${parts.length ? ` Overgezet: ${parts.join(', ')}.` : ' Er was niets over te zetten.'}`;
}

export function ReplaceDevice({ device, all, homeId, onNotice }: { device: Device; all: Device[]; homeId: string; onNotice: (s: string) => void }) {
  const qc = useQueryClient();
  const [target, setTarget] = useState('');
  const candidates = replacementCandidates(device, all);
  const replace = useMutation({
    mutationFn: (id: string) => api.replaceDevice(device.id, id),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: qk.devices(homeId) });
      qc.invalidateQueries({ queryKey: qk.zones(homeId) });
      qc.invalidateQueries({ queryKey: qk.buttons(homeId) });
      onNotice(replaceSummary(r));
    },
  });
  if (!candidates.length) return <p className="small muted" style={{ margin: 0 }}>Vervangen: koppel eerst het nieuwe apparaat; daarna kun je het hier kiezen.</p>;
  return (
    <form
      className="dv-inline-form"
      onSubmit={(e) => {
        e.preventDefault();
        const n = candidates.find((c) => c.id === target);
        if (!n) return;
        if (window.confirm(`${device.name} vervangen door ${n.name}? Rollen, zonekoppelingen en knopkoppelingen gaan over naar ${n.name}. ${device.name} krijgt de status ‘vervangen’; de geschiedenis van beide blijft bewaard.`)) replace.mutate(n.id);
      }}
    >
      <label className="field">
        Vervangen door
        <select value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="">Kies het nieuwe apparaat…</option>
          {candidates.map((c) => <option key={c.id} value={c.id}>{c.name}{c.model ? ` (${c.model})` : ''}</option>)}
        </select>
      </label>
      <ActionButton type="submit" className="btn" disabled={!target || replace.isPending}>Vervangen</ActionButton>
      {replace.error && <ErrorNote error={replace.error} />}
    </form>
  );
}

export function archiveMessage(d: Device): string {
  const parts = [`${d.name} archiveren?`, 'Rollen en zonekoppelingen stoppen en wachtende opdrachten vervallen.'];
  if (d.kind === 'button') parts.push('De knopkoppelingen van deze knop gaan uit.');
  if (d.protected) parts.push('Let op: dit is een beschermd apparaat (boiler); het wordt daarna niet meer bewaakt.');
  parts.push('De metingen blijven bewaard en je kunt het apparaat later terugzetten.');
  return parts.join(' ');
}

export function ArchiveDevice({ device, homeId, onNotice }: { device: Device; homeId: string; onNotice: (s: string) => void }) {
  const qc = useQueryClient();
  const archive = useMutation({
    mutationFn: () => api.archiveDevice(device.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.devices(homeId) });
      qc.invalidateQueries({ queryKey: qk.zones(homeId) });
      onNotice(`${device.name} is gearchiveerd. Terugzetten kan onder ‘Gearchiveerde apparaten’.`);
    },
  });
  return (
    <div className="stack tight">
      <div>
        <ActionButton className="btn danger" disabled={archive.isPending} onClick={() => window.confirm(archiveMessage(device)) && archive.mutate()}>
          Archiveren
        </ActionButton>
      </div>
      {archive.error && <ErrorNote error={archive.error} />}
    </div>
  );
}
