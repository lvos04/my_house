import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { EnergyBars, MeterList, partMeters } from '../components/energy';
import { energy } from './fixtures';

describe('energy quality labels', () => {
  it('labels measured and derived meters', () => {
    render(<MeterList meters={partMeters(energy)} currency="EUR" />);
    const items = screen.getAllByRole('listitem');
    expect(items.map((li) => li.textContent)).toEqual([
      expect.stringContaining('Airco'),
      expect.stringContaining('Boiler'),
      expect.stringContaining('Overig huis'),
    ]);
    expect(within(items[0]).getByText('gemeten')).toBeInTheDocument();
    expect(within(items[2]).getByText('afgeleid')).toBeInTheDocument();
    expect(within(items[2]).getByText('onbekende apparaten')).toBeInTheDocument();
  });

  it('excludes grid totals from parts and marks incomplete buckets', () => {
    expect(partMeters(energy).map((m) => m.role)).toEqual(['airco', 'boiler', 'rest']);
    const { container } = render(<EnergyBars summary={energy} tz="Europe/Amsterdam" />);
    const bars = container.querySelectorAll('.bar');
    expect(bars).toHaveLength(2);
    expect(bars[0]).not.toHaveClass('incomplete');
    expect(bars[1]).toHaveClass('incomplete');
    expect(bars[1].getAttribute('aria-label')).toContain('onvolledig');
  });
});
