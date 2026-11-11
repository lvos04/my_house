// Onafhankelijke ontwerpreview (stap 5, docs/ontwerp/PEIL_REVIEW.md): verouderd is nooit groen, een nooit
// opgehaalde pagina zonder verbinding is Onbekend (geen eindeloos "Laden…"), elke pagina heeft ook tijdens het
// laden één h1, en de lijngrafiek "Laatste 24 uur" heeft een schaal.
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ConnectionProvider } from '../state';
import { Loading } from '../components/bits';
import { StatusBadge } from '../components/peil/StatusBadge';
import { HistoryChart } from '../pages/Climate';
import { BACKUP_STATUS_LABEL } from '../i18n';
import type { ZoneHistory } from '../api/types';

function verbinding(children: ReactNode, online: boolean) {
  return (
    <ConnectionProvider initial={{ apiReachable: online, wsState: online ? 'open' : 'closed', lastSeen: new Date(2026, 10, 9, 15, 54) }}>
      {children}
    </ConnectionProvider>
  );
}

describe('verouderd is nooit groen (Peil §1.1, §4.4)', () => {
  it('Gezond wordt Verouderd zodra de app offline is; Beperkt en Storing blijven staan', () => {
    render(verbinding(<><StatusBadge status="healthy" /><StatusBadge status="degraded" /><StatusBadge status="down" /></>, false));
    expect(screen.queryByText('Gezond')).not.toBeInTheDocument();
    const verouderd = screen.getByText('Verouderd').closest('[data-status]')!;
    expect(verouderd).toHaveAttribute('data-status', 'stale');
    expect(verouderd.className).not.toMatch(/healthy/);
    expect(screen.getByText('Beperkt').closest('[data-status]')).toHaveAttribute('data-status', 'degraded');
    expect(screen.getByText('Storing').closest('[data-status]')).toHaveAttribute('data-status', 'down');
  });

  it('met verbinding blijft Gezond gewoon Gezond', () => {
    render(verbinding(<StatusBadge status="healthy" />, true));
    expect(screen.getByText('Gezond').closest('[data-status]')).toHaveAttribute('data-status', 'healthy');
  });
});

describe('laden en nooit opgehaald (Peil §9 offline, C-17/18)', () => {
  it('online: "Laden…" met aria-busy', () => {
    render(verbinding(<Loading />, true));
    expect(screen.getByRole('status')).toHaveTextContent('Laden…');
    expect(screen.getByRole('status')).toHaveAttribute('aria-busy', 'true');
  });

  it('offline: Onbekend met de reden, geen "Laden…" en niet bezig', () => {
    render(verbinding(<Loading titel="Verlichting" />, false));
    const s = screen.getByRole('status');
    expect(s).toHaveTextContent(/^Onbekend — nog niet opgehaald\./);
    expect(s).not.toHaveTextContent('Laden');
    expect(s).not.toHaveAttribute('aria-busy');
    expect(s).toHaveClass('note', 'unknown');
    // Ook tijdens het laden heeft de pagina één h1.
    expect(screen.getByRole('heading', { level: 1, name: 'Verlichting' })).toBeInTheDocument();
  });

  it('een back-up die er staat heet "Gemaakt", niet "Gelukt" (Peil §11.1)', () => {
    expect(BACKUP_STATUS_LABEL.ok).toBe('Gemaakt');
  });
});

describe('lijngrafiek "Laatste 24 uur" heeft een schaal (Peil §10)', () => {
  it('toont graden op ronde rasterlijnen en de begin- en eindtijd; de toegankelijke naam blijft gelijk', () => {
    const history: ZoneHistory = {
      temperature: [['2026-11-09T08:00:00Z', 19.4], ['2026-11-09T20:00:00Z', 21.2]],
      target: [['2026-11-09T08:00:00Z', 21]],
    };
    const { container } = render(<HistoryChart history={history} tz="Europe/Amsterdam" />);
    expect(screen.getByRole('img')).toHaveAccessibleName('Gemeten en doeltemperatuur, bereik 18° tot 22°, laatste meting 21,2°');
    expect([...container.querySelectorAll('.hist-tick')].map((e) => e.textContent)).toEqual(['18°', '19°', '20°', '21°', '22°']);
    // Begin links en eind rechts; daartussen hele uren (in de browser pas zichtbaar als de grafiek breed genoeg is).
    expect([...container.querySelectorAll('.hist-x span:not(.hist-x-tussen)')].map((e) => e.textContent)).toEqual(['09:00', '21:00']);
    expect([...container.querySelectorAll('.hist-x .hist-x-tussen')].map((e) => e.textContent)).toEqual(['12:00', '14:00', '16:00', '18:00']);
    expect(container.querySelectorAll('svg line')).toHaveLength(5);
  });
});
