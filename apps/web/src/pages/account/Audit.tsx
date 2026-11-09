import { useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { qk } from '../../api/keys';
import type { AuditEntry } from '../../api/types';
import { Empty, ErrorNote, Loading } from '../../components/bits';
import { dateTime } from '../../format';
import { AUDIT_ACTION_LABEL, t } from '../../i18n';

export const AUDIT_PAGE = 50;

/** One readable line for the detail column ("PATCH /api/v1/users/{user_id}" stays as is). */
export function auditDetail(a: AuditEntry): string {
  const d = a.detail ?? {};
  const parts: string[] = [];
  for (const [k, v] of Object.entries(d)) {
    if (v === null || v === undefined || k === 'params') continue;
    if (k === 'status' && a.action === 'request') continue;
    const val = typeof v === 'object' ? JSON.stringify(v) : String(v);
    parts.push(`${k}: ${val}`);
  }
  return parts.join(' · ');
}

/** Audit log with "Meer laden" paging (before = id of the last row) and filters. Admin only. */
export function AuditPanel() {
  const [filter, setFilter] = useState<{ actor: string; action: string }>({ actor: '', action: '' });
  const [draft, setDraft] = useState(filter);
  const [cursors, setCursors] = useState<(number | undefined)[]>([undefined]);
  return (
    <div className="stack">
      <h3>Logboek</h3>
      <p className="small muted" style={{ margin: 0 }}>Wie wat wanneer heeft gewijzigd, nieuwste eerst. Wachtwoorden en verzoekinhoud worden nooit vastgelegd.</p>
      <form
        className="form-grid"
        aria-label="Logboek filteren"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          setFilter({ actor: draft.actor.trim(), action: draft.action });
          setCursors([undefined]);
        }}
      >
        <label className="field">Door (gebruiker of telefoon)<input value={draft.actor} onChange={(e) => setDraft({ ...draft, actor: e.target.value })} /></label>
        <label className="field">Soort
          <select value={draft.action} onChange={(e) => setDraft({ ...draft, action: e.target.value })}>
            <option value="">Alles</option>
            {Object.entries(AUDIT_ACTION_LABEL).sort((a, b) => a[1].localeCompare(b[1], 'nl')).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <button type="submit" className="btn">Filteren</button>
      </form>
      <div className="table-wrap">
        <table className="stack-mobile">
          <caption className="sr-only">Logboek</caption>
          <thead><tr><th scope="col">Moment</th><th scope="col">Door</th><th scope="col">Wat</th><th scope="col">Waarop</th><th scope="col">Details</th></tr></thead>
          {cursors.map((before, i) => (
            <AuditPage
              key={`${filter.actor}|${filter.action}|${before ?? 'first'}`}
              before={before}
              actor={filter.actor}
              action={filter.action}
              isLast={i === cursors.length - 1}
              onMore={(next) => setCursors((c) => [...c, next])}
            />
          ))}
        </table>
      </div>
    </div>
  );
}

function AuditPage({ before, actor, action, isLast, onMore }: { before?: number; actor: string; action: string; isLast: boolean; onMore: (before: number) => void }) {
  const q = useQuery({
    queryKey: [...qk.audit, { before, actor, action }],
    queryFn: () => api.audit({ limit: AUDIT_PAGE, before, actor: actor || undefined, action: action || undefined }),
    staleTime: 30_000,
  });
  const rows = Array.isArray(q.data) ? q.data : [];
  return (
    <tbody>
      {q.isLoading && <tr><td colSpan={5}><Loading /></td></tr>}
      {q.error && <tr><td colSpan={5}><ErrorNote error={q.error} /></td></tr>}
      {q.data && rows.length === 0 && before === undefined && <tr><td colSpan={5}><Empty>Geen regels gevonden.</Empty></td></tr>}
      {rows.map((a) => (
        <tr key={a.id}>
          <td data-label="Moment">{dateTime(a.at)}</td>
          <td data-label="Door">{a.actor}</td>
          <td data-label="Wat">{t(AUDIT_ACTION_LABEL, a.action)}</td>
          <td data-label="Waarop" className="small break">{a.target ?? '–'}</td>
          <td data-label="Details" className="small muted break">{auditDetail(a)}</td>
        </tr>
      ))}
      {isLast && rows.length === AUDIT_PAGE && (
        <tr>
          <td colSpan={5}>
            <button type="button" className="btn" onClick={() => onMore(rows[rows.length - 1].id)}>Meer laden</button>
          </td>
        </tr>
      )}
      {isLast && q.data && rows.length > 0 && rows.length < AUDIT_PAGE && (
        <tr><td colSpan={5} className="small muted">Einde van het logboek.</td></tr>
      )}
    </tbody>
  );
}
