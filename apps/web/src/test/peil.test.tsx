// Peil-omzetting fase 2 (docs/ontwerp/PEIL_MIGRATIE.md §5, ADR-0009): status als icoon + woord + vorm,
// de shadcn/ui-componenten in de React 18-variant en de LichtTegel (WCAG 2.5.3).
import { createRef } from 'react';
import { render, screen } from '@testing-library/react';
import { StatusBadge, statusVan } from '../components/peil/StatusBadge';
import { ErnstLabel, ernstVan } from '../components/peil/ErnstLabel';
import { BeleidsSlot } from '../components/peil/BeleidsSlot';
import { Button, buttonVariants } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Card } from '../components/ui/card';
import { LightTile } from '../pages/Overview';
import { mockApi, Providers, viewer } from './helpers';
import type { LightGroup } from '../api/types';

describe('Status als icoon + woord + vorm (Peil C-03, T-20, C-13)', () => {
  it('StatusBadge gebruikt de vaste woorden en markeert de status voor forced colors', () => {
    render(
      <>
        <StatusBadge status={statusVan('ok')} />
        <StatusBadge status={statusVan('warn')} />
        <StatusBadge status={statusVan('error')} />
        <StatusBadge status={statusVan('unknown')} />
        <StatusBadge status={statusVan('not_configured')} />
        <StatusBadge status="stale" />
      </>,
    );
    for (const [woord, status] of [['Gezond', 'healthy'], ['Beperkt', 'degraded'], ['Storing', 'down'], ['Onbekend', 'unknown'], ['Niet ingesteld', 'not_configured'], ['Verouderd', 'stale']]) {
      const el = screen.getByText(woord).closest('[data-status]')!;
      expect(el).toHaveAttribute('data-status', status);
      expect(el.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    }
  });

  it('ErnstLabel zet de ernst van meldingen om naar Info · Middel · Hoog', () => {
    render(<><ErnstLabel ernst={ernstVan('info')} /><ErnstLabel ernst={ernstVan('warn')} /><ErnstLabel ernst={ernstVan('error')} /></>);
    expect(screen.getByText('Info').closest('[data-ernst]')).toHaveTextContent('Ernst: Info');
    expect(screen.getByText('Middel')).toBeInTheDocument();
    expect(screen.getByText('Hoog')).toBeInTheDocument();
  });

  it('BeleidsSlot heeft geen knop of schakelaar in de DOM', () => {
    render(<BeleidsSlot>Alleen meten — schakelen geblokkeerd.</BeleidsSlot>);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    expect(screen.getByText('Alleen meten')).toBeInTheDocument();
  });
});

describe('shadcn/ui-componenten in de React 18-variant (forwardRef)', () => {
  it('Button, Badge en Card geven de ref door en houden de haakklassen', () => {
    const b = createRef<HTMLButtonElement>();
    const s = createRef<HTMLSpanElement>();
    const c = createRef<HTMLDivElement>();
    render(
      <>
        <Button ref={b} variant="primair" size="klein">Opslaan</Button>
        <Badge ref={s} variant="verwarmen">Verwarmen</Badge>
        <Card ref={c}>Kaart</Card>
        <Button asChild variant="stil"><a href="/help">Help</a></Button>
      </>,
    );
    expect(b.current).toBeInstanceOf(HTMLButtonElement);
    expect(b.current).toHaveClass('btn', 'primary', 'small');
    expect(b.current).toHaveAttribute('type', 'button');
    expect(s.current).toHaveClass('pill', 'heat');
    expect(c.current).toHaveClass('card');
    expect(screen.getByRole('link', { name: 'Help' })).toHaveClass('btn', 'ghost');
    expect(buttonVariants({ variant: 'gevaar' })).toBe('btn danger');
    expect(Button.displayName).toBe('Button');
  });
});

describe('LichtTegel (WCAG 2.5.3: zichtbare tekst in de toegankelijke naam)', () => {
  const group = (over: Partial<LightGroup>): LightGroup =>
    ({ id: 'g1', name: 'Keuken', on: false, reachable: true, brightness: null, scene: null, lights_on: 0, lights_total: 2, lights_reachable: 2, hidden: false, all_lights: false, ...over }) as LightGroup;

  it('de naam begint met de zichtbare tekst; aria-pressed zegt aan/uit; onbereikbaar = Onbekend met reden', () => {
    mockApi(() => undefined);
    render(
      <Providers>
        <LightTile group={group({})} />
        <LightTile group={group({ id: 'g2', name: 'Tuin', reachable: false })} />
        <LightTile group={group({ id: 'g3', name: 'Woonkamer', on: true, scene: 'Lezen', brightness: 80 })} />
      </Providers>,
    );
    expect(screen.getByRole('button', { name: 'Keuken Uit' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'Woonkamer Lezen · 80%' })).toHaveAttribute('aria-pressed', 'true');
    const tuin = screen.getByRole('button', { name: 'Tuin Onbekend — niet bereikbaar' });
    expect(tuin).toBeDisabled();
    expect(tuin).toHaveClass('is-unreachable');
  });

  it('een kijker ziet de tegel zonder knop', () => {
    mockApi(() => undefined);
    render(<Providers user={viewer}><LightTile group={group({ on: true })} /></Providers>);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getByText('Keuken')).toBeInTheDocument();
  });
});
