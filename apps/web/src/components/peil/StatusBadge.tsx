// StatusBadge (Peil C-03): gezondheid altijd als icoon + vast woord + vorm (pil; stippelrand bij onbekend).
// Groen alleen voor "gemeten en goed" (ONTWERPTAAL §4.1). Bij Onbekend staat de reden er altijd naast.
import type { ReactNode } from 'react';
import { CircleCheck, CircleDashed, CircleHelp, CirclePause, ClockAlert, Hourglass, Link2Off, OctagonX, TriangleAlert, type LucideIcon } from 'lucide-react';
import { cn } from '../../lib/utils';

export type StatusSoort = 'healthy' | 'degraded' | 'down' | 'unknown' | 'stale' | 'paused' | 'unlinked' | 'not_yet' | 'not_configured';

const STATUS: Record<StatusSoort, { woord: string; Icon: LucideIcon; vorm: string; icoon?: string }> = {
  healthy: { woord: 'Gezond', Icon: CircleCheck, vorm: 'border-transparent text-healthy-ink', icoon: 'text-healthy' },
  degraded: { woord: 'Beperkt', Icon: TriangleAlert, vorm: 'border-degraded bg-degraded-bg text-degraded-ink' },
  down: { woord: 'Storing', Icon: OctagonX, vorm: 'border-down bg-down-bg text-down-ink' },
  unknown: { woord: 'Onbekend', Icon: CircleHelp, vorm: 'border-dashed border-unknown bg-unknown-bg text-unknown-ink' },
  stale: { woord: 'Verouderd', Icon: ClockAlert, vorm: 'arcering border-dashed border-stale text-unknown-ink' },
  paused: { woord: 'Gepauzeerd', Icon: CirclePause, vorm: 'border-paused bg-paused-bg text-paused-ink' },
  unlinked: { woord: 'Niet gekoppeld', Icon: Link2Off, vorm: 'border-dashed border-unknown bg-unknown-bg text-unknown-ink' },
  not_yet: { woord: 'Nog niet beschikbaar', Icon: Hourglass, vorm: 'border-border-strong bg-muted text-foreground' },
  // "Nog niet ingesteld" is een open toestand, geen storing (regel 12): neutraal.
  not_configured: { woord: 'Niet ingesteld', Icon: CircleDashed, vorm: 'border-border-strong bg-muted text-foreground' },
};

/** Vaste woorden per status (ONTWERPTAAL §11.1). */
export const STATUS_WOORD: Record<StatusSoort, string> = Object.fromEntries(
  Object.entries(STATUS).map(([k, v]) => [k, v.woord]),
) as Record<StatusSoort, string>;

/** Toestand van een statuscontrole of melding van de API → Peil-status. */
export function statusVan(state: string | null | undefined): StatusSoort {
  switch (state) {
    case 'ok':
      return 'healthy';
    case 'warn':
      return 'degraded';
    case 'error':
      return 'down';
    case 'not_configured':
      return 'not_configured';
    default:
      return 'unknown';
  }
}

export function StatusIcon({ status, className }: { status: StatusSoort; className?: string }) {
  const { Icon, icoon } = STATUS[status];
  return <Icon size={18} strokeWidth={2} className={cn('status-icon shrink-0', icoon, className)} aria-hidden="true" />;
}

export function StatusBadge({ status, label, className, children }: { status: StatusSoort; label?: string; className?: string; children?: ReactNode }) {
  const s = STATUS[status];
  return (
    <span
      data-status={status}
      className={cn(
        'status-badge inline-flex w-fit max-w-full items-center gap-1.5 min-h-8 px-2.5 rounded-full border-2 text-sm font-semibold',
        s.vorm,
        className,
      )}
    >
      <StatusIcon status={status} />
      <span>{label ?? s.woord}</span>
      {children}
    </span>
  );
}
