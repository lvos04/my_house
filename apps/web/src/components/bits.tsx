import type { ButtonHTMLAttributes, ReactNode } from 'react';
import type { CheckState, Quality, Severity } from '../api/types';
import { QUALITY_LABEL } from '../format';
import { useCanAct } from '../state';

export function QualityBadge({ quality }: { quality: Quality }) {
  return <span className={`q ${quality}`}>{QUALITY_LABEL[quality]}</span>;
}

export function Dot({ state }: { state: CheckState | Severity | 'not_configured' }) {
  return <span className={`dot ${state}`} aria-hidden="true" />;
}

/** A button that is automatically disabled while the app is offline. */
export function ActionButton({ disabled, children, title, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { children: ReactNode }) {
  const canAct = useCanAct();
  return (
    <button type="button" {...rest} disabled={disabled || !canAct} title={!canAct ? 'Niet beschikbaar: geen verbinding' : title}>
      {children}
    </button>
  );
}

export function Loading({ label = 'Laden…' }: { label?: string }) {
  return <p className="muted" role="status">{label}</p>;
}

export function ErrorNote({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : 'Onbekende fout';
  return <div className="note error" role="alert">{msg}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="muted small">{children}</p>;
}
