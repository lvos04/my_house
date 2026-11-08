import { useConnection } from '../state';
import { time } from '../format';
import { CloudOffIcon } from './Icons';

export function OfflineBanner() {
  const { offline, lastSeen } = useConnection();
  if (!offline) return null;
  return (
    <div className="offline-banner" role="status" aria-live="polite">
      <CloudOffIcon />
      <span>
        Geen verbinding met de server. Je ziet de laatst bekende status
        {lastSeen ? <> (laatst bekend {time(lastSeen)})</> : null}. Bediening is uitgeschakeld.
      </span>
    </div>
  );
}
