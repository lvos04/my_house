// A restore replaces the whole database, sessions included, so the admin is logged out halfway. The job
// id is kept in localStorage so both the login page and Systeem can follow it (the job endpoint needs
// no login) and continue with the comparison afterwards.
import { useQuery } from '@tanstack/react-query';
import { api, isApiError } from '../../api/client';
import type { RestoreJob, RestoreMode } from '../../api/types';
import { ProgressBar } from '../../components/bits';
import { RESTORE_MODE_LABEL } from '../../i18n';

export const RESTORE_KEY = 'shc.restore';

export interface StoredRestore {
  job_id: string;
  mode: RestoreMode;
  started_at: number;
  filename?: string | null;
}

export function loadRestore(): StoredRestore | null {
  try {
    const raw = localStorage.getItem(RESTORE_KEY);
    const v = raw ? (JSON.parse(raw) as StoredRestore) : null;
    return v && typeof v.job_id === 'string' ? v : null;
  } catch {
    return null;
  }
}
export function saveRestore(r: StoredRestore) {
  try {
    localStorage.setItem(RESTORE_KEY, JSON.stringify(r));
  } catch {
    /* storage blocked: progress is still shown while this page stays open */
  }
}
export function clearRestore() {
  try {
    localStorage.removeItem(RESTORE_KEY);
  } catch {
    /* ignore */
  }
}

export const RESTORE_POLL_MS = 2000;

/** Poll a restore job until it is done or failed. `missing` = the server no longer knows the job
 * (e.g. the API restarted): check the comparison instead. */
export function useRestoreJob(jobId: string | null) {
  const q = useQuery<RestoreJob>({
    queryKey: ['restoreJob', jobId],
    queryFn: () => api.restoreJob(jobId!),
    enabled: Boolean(jobId),
    retry: (n, e) => !isApiError(e, 'not_found') && n < 30,
    retryDelay: RESTORE_POLL_MS,
    refetchInterval: (query) => (query.state.data && query.state.data.state !== 'running' ? false : RESTORE_POLL_MS),
    staleTime: 0,
    gcTime: 60_000,
  });
  const missing = isApiError(q.error) && q.error.status === 404;
  return { job: q.data ?? null, missing, error: missing ? null : q.error };
}

/** Compact progress line for the login page while a restore runs or just finished. */
export function PendingRestoreNotice() {
  const stored = loadRestore();
  const { job, missing } = useRestoreJob(stored?.job_id ?? null);
  if (!stored) return null;
  const state = missing ? 'done' : job?.state ?? 'running';
  return (
    <div className={`note ${state === 'failed' ? 'error' : 'info'}`} role="status" aria-live="polite">
      <strong>Herstel ({RESTORE_MODE_LABEL[stored.mode] ?? stored.mode})</strong>
      {state === 'running' && (
        <>
          <div>Bezig: {job?.step ?? 'gestart'}. Laat deze pagina open.</div>
          <ProgressBar value={null} label="Herstel loopt" />
        </>
      )}
      {state === 'done' && <div>Klaar. Log in (met een account uit de back-up) om per apparaat te vergelijken wat werkt en daarna de veilige modus op te heffen.</div>}
      {state === 'failed' && <div>Mislukt: {job?.error ?? 'onbekende fout'}. Er is niets gewijzigd; log in en probeer het opnieuw.</div>}
    </div>
  );
}
