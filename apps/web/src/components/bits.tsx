import { useState, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, AccessLoginRequired, NetworkError } from '../api/client';
import type { CheckState, Quality, Severity } from '../api/types';
import { QUALITY_LABEL } from '../format';
import { CHECK_STATE_LABEL, ERROR_CODE_LABEL } from '../i18n';
import { useCanAct } from '../state';

export function QualityBadge({ quality }: { quality: Quality }) {
  return <span className={`q ${quality}`}>{QUALITY_LABEL[quality] ?? quality}</span>;
}

/** Coloured status dot. Decorative: always put the state in text next to it. */
export function Dot({ state }: { state: CheckState | Severity | 'not_configured' | (string & {}) }) {
  return <span className={`dot ${state}`} aria-hidden="true" />;
}

/** Dot + Dutch state word, for lists where colour alone would not do. */
export function StateLabel({ state, children }: { state: CheckState | 'not_configured' | (string & {}); children?: ReactNode }) {
  return (
    <span className={`state-line ${state}`}>
      <Dot state={state} />
      {children ?? CHECK_STATE_LABEL[state] ?? state}
    </span>
  );
}

/** A button that is automatically disabled while the app is offline (or the API is starting). */
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

/** Error box with the server's Dutch message; links to the right settings for known codes. */
export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  let msg = error instanceof Error && error.message ? error.message : 'Er ging iets mis. Probeer het opnieuw.';
  let extra: ReactNode = null;
  if (error instanceof ApiError) {
    if (!error.message) msg = ERROR_CODE_LABEL[error.code] ?? msg;
    if (['lan_url_required', 'lan_url_invalid', 'remote_url_invalid'].includes(error.code))
      extra = <> <Link to="/systeem#adressen">Naar Systeem › Adressen</Link></>;
    if (error.retryAfter && error.code === 'rate_limited') extra = <> (wacht {error.retryAfter} s)</>;
    if (error.detail.errors && error.detail.errors.length > 1) {
      msg = 'Controleer het volgende:';
      extra = (
        <ul className="plain-list">
          {error.detail.errors.map((e) => <li key={e}>{e}</li>)}
        </ul>
      );
    }
  } else if (error instanceof AccessLoginRequired || error instanceof NetworkError) {
    msg = error.message;
  }
  return <div className="note error" role="alert">{msg}{extra}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="muted small">{children}</p>;
}

/** Copies text to the clipboard (diagnostics). Falls back to selecting a hidden textarea. */
export function CopyButton({ text, label = 'Kopiëren', className = 'btn' }: { text: string; label?: string; className?: string }) {
  const [done, setDone] = useState<'ok' | 'fail' | null>(null);
  const copy = async () => {
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.position = 'absolute';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand?.('copy');
        document.body.removeChild(ta);
        if (!ok) throw new Error('copy');
      }
      setDone('ok');
    } catch {
      setDone('fail');
    }
    setTimeout(() => setDone(null), 3000);
  };
  return (
    <>
      <button type="button" className={className} onClick={copy}>{label}</button>
      <span className="small muted" role="status" aria-live="polite">
        {done === 'ok' ? 'Gekopieerd.' : done === 'fail' ? 'Kopiëren lukte niet; selecteer de tekst zelf.' : ''}
      </span>
    </>
  );
}

/** Accessible progress bar (0..1, or null = unknown). */
export function ProgressBar({ value, label }: { value: number | null; label: string }) {
  const pctv = value === null ? null : Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div className="progress" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pctv ?? undefined} aria-valuetext={pctv === null ? 'bezig' : `${pctv}%`}>
      <div className={`progress-fill${pctv === null ? ' indeterminate' : ''}`} style={pctv === null ? undefined : { width: `${pctv}%` }} />
    </div>
  );
}

/** Ja/nee in Dutch for booleans; "–" for unknown. */
export function yesNo(v: boolean | null | undefined): string {
  return v === true ? 'ja' : v === false ? 'nee' : '–';
}
