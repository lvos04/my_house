// ErnstLabel (Peil T-20): rechthoek met een dikke linkerbalk (6 px) en een vast woord. Ernst is geen
// gezondheid: geen statusicoon, geen pilvorm.
import { cn } from '../../lib/utils';

export type Ernst = 'kritiek' | 'hoog' | 'middel' | 'laag' | 'info';

const ERNST: Record<Ernst, { woord: string; vorm: string }> = {
  kritiek: { woord: 'Kritiek', vorm: 'bg-destructive text-destructive-foreground border-down-ink' },
  hoog: { woord: 'Hoog', vorm: 'bg-down-bg text-down-ink border-down' },
  middel: { woord: 'Middel', vorm: 'bg-muted text-foreground border-foreground' },
  laag: { woord: 'Laag', vorm: 'bg-unknown-bg text-unknown-ink border-unknown' },
  info: { woord: 'Info', vorm: 'bg-info-bg text-info-ink border-info' },
};

/** Ernst van een melding van de API (info/warn/error) → vast ernstwoord (PEIL_MIGRATIE §10). */
export function ernstVan(severity: string): Ernst {
  return severity === 'error' ? 'hoog' : severity === 'warn' ? 'middel' : 'info';
}

export function ErnstLabel({ ernst, className }: { ernst: Ernst; className?: string }) {
  const e = ERNST[ernst];
  return (
    <span data-ernst={ernst} className={cn('ernst-label inline-flex w-fit items-center min-h-8 pl-2 pr-2.5 rounded-sm border-l-[6px] text-sm font-semibold', e.vorm, className)}>
      <span className="sr-only">Ernst: </span>
      {e.woord}
    </span>
  );
}
