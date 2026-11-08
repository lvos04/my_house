import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PairingQr } from '../pages/System';

describe('phone pairing QR', () => {
  it('renders the qr_payload as an image plus code and addresses', async () => {
    const qr_payload = JSON.stringify({ v: 1, type: 'shc-pair', code: 'K7Q4-9XPM', lan: 'http://192.168.1.20:8080', remote: 'https://huis.voorbeeld.nl', home: 'Thuis' });
    render(<PairingQr pairing={{ code: 'K7Q4-9XPM', expires_at: '2026-11-08T14:50:00Z', qr_payload }} />);
    expect(screen.getByTestId('pair-code')).toHaveTextContent('K7Q4-9XPM');
    expect(screen.getByText('http://192.168.1.20:8080')).toBeInTheDocument();
    expect(screen.getByText('https://huis.voorbeeld.nl')).toBeInTheDocument();
    const img = await screen.findByRole('img', { name: /K7Q4-9XPM/ });
    expect(img.getAttribute('src')).toMatch(/^data:image\/png;base64,/);
  });
});
