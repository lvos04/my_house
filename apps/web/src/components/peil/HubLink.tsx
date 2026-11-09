// Link "Terug naar de Hub" (O-11): stil-knopstijl, zelfde tabblad (terugkeren, geen nieuw venster).
// Verborgen zolang het adres onbekend of ongeldig is (src/hub.ts).
import { useHubAdres } from '../../hub';
import { BackIcon } from '../Icons';

export function HubLink({ className = '' }: { className?: string }) {
  const url = useHubAdres();
  if (!url) return null;
  return (
    <a className={`btn ghost hub-link ${className}`.trim()} href={url} data-testid="hub-link">
      <BackIcon />
      Terug naar de Hub
    </a>
  );
}
