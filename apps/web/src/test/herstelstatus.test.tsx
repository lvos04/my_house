import { afterEach, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { RestoreSection } from '../pages/system/Restore';
import { PendingRestoreNotice, saveRestore } from '../pages/system/restoreState';
import { mockApi, newClient, Providers } from './helpers';

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

const id = 'fictieve-hersteltaak';
it.each([
  [404, { detail: { code: 'not_found', message: 'Herstelopdracht niet gevonden.' } }],
  [200, { state: 'done' }],
  [200, { job_id: id, state: 'done', finished_at: '2026-11-01T11:00:00Z', report: {} }],
] as const)('toont onbekend bij ontbrekende taak of onvolledig antwoord (%s)', async (status, body) => {
  saveRestore({ job_id: id, filename: 'proef.shcbak', mode: 'migration', started_at: Date.now() });
  const client = newClient();
  mockApi((_method, url) => url.endsWith('/jobs/' + id) ? { status, body } : { body: {} });
  render(<Providers client={client}><RestoreSection safeMode={false} /><PendingRestoreNotice /></Providers>);
  await waitFor(() => expect(client.getQueryState(['restoreJob', id])?.status).toBe(status === 404 ? 'error' : 'success'));
  await waitFor(() => expect(screen.queryByText('Bezig: gestart. Laat deze pagina open.')).toBeNull());
  const systeem = Boolean(screen.queryByText(/^Herstel klaar/));
  const inloggen = Boolean(screen.queryByText(/^Klaar\. Log in/));
  expect(screen.getAllByText(/Onbekende uitkomst/)).toHaveLength(2);
  expect({ systeem, inloggen }).toEqual({ systeem: false, inloggen: false });
});
