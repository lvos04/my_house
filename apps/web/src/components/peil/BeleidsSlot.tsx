// BeleidsSlot (Peil C-13): "Alleen meten" met een verbodsteken, waar de gebruiker de schakelaar zou zoeken.
// Er staat bewust géén knop of schakelaar in de DOM (boiler: schakelen is altijd uitgeschakeld).
import type { ReactNode } from 'react';
import { Ban } from 'lucide-react';
import { cn } from '../../lib/utils';

export function BeleidsSlot({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('beleids-slot flex flex-col gap-2 rounded-lg border-2 border-gate bg-gate-bg p-3 text-gate-ink', className)}>
      <span className="inline-flex w-fit items-center gap-1.5 rounded-sm border-2 border-gate px-2 text-sm font-semibold">
        <Ban size={18} strokeWidth={2} aria-hidden="true" />
        Alleen meten
      </span>
      <span className="text-sm">{children}</span>
    </div>
  );
}
