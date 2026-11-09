import { useConnection } from '../state';
import { useSnapshotInfo } from '../snapshot';
import { time } from '../format';
import { SIGNOUT_REASON_LABEL } from '../i18n';
import { BusyIcon, CloudOffIcon, InfoIcon } from './Icons';

/** One status line at the top of every page: Access login needed > offline > API starting >
 * live updates refused. Renders nothing when all is well. */
export function OfflineBanner() {
  const { offline, lastSeen, starting, accessLoginUrl, liveDenied } = useConnection();
  const { savedAt } = useSnapshotInfo();

  if (accessLoginUrl)
    return (
      <div className="offline-banner" role="status" aria-live="polite">
        <CloudOffIcon />
        <span>
          Je inlogsessie bij Cloudflare Access is verlopen. <a href={accessLoginUrl}>Opnieuw inloggen bij Cloudflare</a>
        </span>
      </div>
    );
  if (offline) {
    const known = lastSeen ?? (savedAt ? new Date(savedAt) : null);
    return (
      <div className="offline-banner" role="status" aria-live="polite">
        <CloudOffIcon />
        <span>
          Geen verbinding met de server. Je ziet de laatst bekende status
          {known ? <> (laatst bekend {time(known)})</> : null}. Bediening is uitgeschakeld.
        </span>
      </div>
    );
  }
  if (starting)
    return (
      <div className="offline-banner info" role="status" aria-live="polite">
        <BusyIcon />
        <span>{starting} Bediening is even uitgeschakeld.</span>
      </div>
    );
  if (liveDenied)
    return (
      <div className="offline-banner info" role="status" aria-live="polite">
        <InfoIcon />
        <span>
          Live-updates zijn nu niet beschikbaar{SIGNOUT_REASON_LABEL[liveDenied] && liveDenied === 'origin_mismatch' ? ' (adres komt niet overeen; controleer de proxy-instelling)' : ''}.
          Gegevens worden ververst bij het openen van een pagina; bevestigingen van commando's kunnen later komen.
        </span>
      </div>
    );
  return null;
}
