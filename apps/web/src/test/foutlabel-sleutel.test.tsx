import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ApiError } from '../api/client';
import { ErrorNote } from '../components/bits';
import { ERROR_CODE_LABEL } from '../i18n';
import { Providers } from './helpers';

// KLEIN-70 (A-23): vangnettekst voor 503 secret_key_invalid, gelijk aan de vaste servertekst
// (SLEUTEL_ONBRUIKBAAR_TEKST in apps/api/src/shc_api/errors.py, KLEIN-44).
const SERVERTEKST = 'De sleutel van deze server ontbreekt of is ongeldig; deze actie is niet mogelijk.';

describe('ERROR_CODE_LABEL secret_key_invalid', () => {
  it('heeft een eigen label gelijk aan de servertekst', () => {
    expect(ERROR_CODE_LABEL.secret_key_invalid).toBe(SERVERTEKST);
  });

  it('ErrorNote toont het label als de server geen tekst meestuurt', () => {
    render(
      <Providers>
        <ErrorNote error={new ApiError(503, 'secret_key_invalid', '')} />
      </Providers>,
    );
    expect(screen.getByText(SERVERTEKST)).toBeInTheDocument();
    expect(screen.queryByText('Er ging iets mis. Probeer het opnieuw.')).not.toBeInTheDocument();
  });

  it('ErrorNote houdt de tekst van de server als die er is', () => {
    render(
      <Providers>
        <ErrorNote error={new ApiError(503, 'secret_key_invalid', 'Andere tekst van de server.')} />
      </Providers>,
    );
    expect(screen.getByText('Andere tekst van de server.')).toBeInTheDocument();
    expect(screen.queryByText(SERVERTEKST)).not.toBeInTheDocument();
  });
});
