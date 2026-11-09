import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import { useCan, useUser } from '../user';
import { useLogout } from '../components/useLogout';
import { USER_ROLE_LABEL } from '../i18n';
import { StatusSection, VersionCard } from './system/Status';
import { AddressSettings, Homes, NotifySettings, SafeModeCard } from './system/Settings';
import { BackupsSection } from './system/Backups';
import { RestoreSection } from './system/Restore';

// Kept here for existing imports (tests, other pages).
export { PairingQr } from './account/Phones';
export { AddressSettings } from './system/Settings';

function AccountCard() {
  const user = useUser();
  const logout = useLogout();
  if (!user) return null;
  return (
    <section aria-labelledby="acc-h" className="card">
      <h2 id="acc-h">Account en toegang</h2>
      <p style={{ margin: 0 }}>Ingelogd als <strong>{user.username}</strong> ({USER_ROLE_LABEL[user.role] ?? user.role}).</p>
      <div className="row">
        <Link className="btn" to="/account">Wachtwoord, gebruikers en telefoons</Link>
        <button type="button" className="btn" disabled={logout.isPending} onClick={() => logout.mutate()}>Uitloggen</button>
      </div>
    </section>
  );
}

export function SystemPage() {
  const can = useCan();
  const status = useQuery({ queryKey: qk.system, queryFn: api.systemStatus, refetchInterval: 60_000 });
  const safeMode = Boolean(status.data?.checks?.some((c) => c.key === 'safe_mode'));
  return (
    <>
      <header className="page-head">
        <div><span className="eyebrow">Beheer</span><h1 tabIndex={-1}>Systeem</h1></div>
        <nav aria-label="Snel naar" className="row">
          {can.admin && <Link className="btn small" to="/installatie">Installatiewizard</Link>}
          {can.admin && <a className="btn small" href="#herstel">Herstellen en verhuizen</a>}
          <Link className="btn small" to="/help">Help</Link>
        </nav>
      </header>
      {safeMode && can.admin && <SafeModeCard />}
      <StatusSection />
      <VersionCard />
      {can.admin ? (
        <>
          <AddressSettings />
          <BackupsSection />
          <NotifySettings />
          <RestoreSection safeMode={safeMode} />
          <Homes />
        </>
      ) : (
        <p className="muted small">Instellingen, back-ups en herstellen zijn alleen beschikbaar voor beheerders.</p>
      )}
      <AccountCard />
    </>
  );
}
