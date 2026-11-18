import type { ReactNode } from 'react';
import { LockKeyhole } from 'lucide-react';

/** Peil C-11 voor een voorwaarde die eerst moet worden vervuld. */
export function GateBlokkade({ label, children, role = 'status' }: {
  label: string; children: ReactNode; role?: 'status' | 'alert';
}) {
  return (
    <div role={role} className="melding-gate grid gap-2 rounded-lg border-2 border-gate bg-gate-bg p-4 text-gate-ink">
      <strong className="flex items-center gap-2"><LockKeyhole size={20} className="shrink-0" aria-hidden="true" />{label}</strong>
      <div>{children}</div>
    </div>
  );
}
