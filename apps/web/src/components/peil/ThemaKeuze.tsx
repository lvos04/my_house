// ThemaKeuze (Peil C-50, O-09): drie tekstknoppen Systeem · Licht · Donker met aria-pressed, als groep
// "Thema". Standaard Systeem. Wisselen zonder herladen; de focus blijft op de knop. Ook een kijker mag dit
// (alleen uiterlijk, alleen op dit apparaat).
import { useId } from 'react';
import { THEMA_KEUZES, useThema } from '../../theme';

export function ThemaKeuze() {
  const [keuze, kies] = useThema();
  const id = useId();
  return (
    <div className="thema-keuze flex flex-col gap-1.5" data-testid="thema-keuze">
      <span id={`${id}-l`} className="text-sm font-semibold text-foreground">Thema</span>
      <div role="group" aria-labelledby={`${id}-l`} aria-describedby={`${id}-h`} className="c-choices thema-knoppen">
        {THEMA_KEUZES.map((t) => (
          <button key={t.key} type="button" aria-pressed={keuze === t.key} onClick={() => kies(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      <span id={`${id}-h`} className="text-sm text-muted-foreground">Systeem volgt de instelling van je apparaat.</span>
    </div>
  );
}
