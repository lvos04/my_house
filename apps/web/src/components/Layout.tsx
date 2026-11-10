import { useEffect, useRef, useState, type ComponentType, type SVGProps } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { User } from '../api/types';
import { useConnection, useHome } from '../state';
import { useLiveUpdates } from '../ws';
import { useSnapshotInfo } from '../snapshot';
import { useLogout } from './useLogout';
import { APP_VERSION } from '../format';
import { USER_ROLE_LABEL } from '../i18n';
import { OfflineBanner } from './OfflineBanner';
import { NextSteps, NoHomeSetup } from './NoHome';
import { BoltIcon, BulbIcon, DeviceIcon, GearIcon, GridIcon, HelpIcon, HomeIcon, LogOutIcon, MenuIcon, ThermoIcon, TrendIcon, UserIcon } from './Icons';
import { Button } from './ui/button';
import { HubLink } from './peil/HubLink';
import { ThemaKeuze } from './peil/ThemaKeuze';

/** Routes that work without a home (the rest show the "Woning aanmaken" form until there is one). */
export const HOME_FREE_PATHS = ['/systeem', '/account', '/installatie', '/help'];

type Icon = ComponentType<SVGProps<SVGSVGElement>>;
interface NavItem {
  to: string;
  label: string;
  icon: Icon;
  end?: boolean;
}
/** Bottom bar on phones: the four daily pages; the rest is under "Meer". */
export const MAIN_NAV: NavItem[] = [
  { to: '/', label: 'Overzicht', icon: GridIcon, end: true },
  { to: '/klimaat', label: 'Klimaat', icon: ThermoIcon },
  { to: '/verlichting', label: 'Licht', icon: BulbIcon },
  { to: '/energie', label: 'Energie', icon: BoltIcon },
];
export const MORE_NAV: NavItem[] = [
  { to: '/optimalisatie', label: 'Optimalisatie', icon: TrendIcon },
  { to: '/apparaten', label: 'Apparaten', icon: DeviceIcon },
  { to: '/systeem', label: 'Systeem', icon: GearIcon },
  { to: '/account', label: 'Account', icon: UserIcon },
  { to: '/help', label: 'Help', icon: HelpIcon },
];
const SIDEBAR_NAV: NavItem[] = [
  { ...MAIN_NAV[0] },
  { ...MAIN_NAV[1] },
  { ...MAIN_NAV[2], label: 'Verlichting' },
  MAIN_NAV[3],
  MORE_NAV[0],
  MORE_NAV[1],
  MORE_NAV[2],
];

function navClass({ isActive }: { isActive: boolean }) {
  return `nav-link${isActive ? ' active' : ''}`;
}

/** "Meer" sheet on phones: the other pages, account and logout. */
function MoreSheet({ user, onClose, onLogout, busy }: { user: User; onClose: () => void; onLogout: () => void; busy: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>('a, button')?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key !== 'Tab' || !ref.current) return;
      // Keep focus inside the dialog.
      const items = Array.from(ref.current.querySelectorAll<HTMLElement>('a[href], button:not([disabled])'));
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div ref={ref} className="sheet" role="dialog" aria-modal="true" aria-labelledby="more-h" onClick={(e) => e.stopPropagation()}>
        <div className="section-head">
          <h2 id="more-h">Meer</h2>
          <Button variant="stil" onClick={onClose}>Sluiten</Button>
        </div>
        <nav aria-label="Meer pagina's" className="sheet-nav">
          {MORE_NAV.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={navClass} onClick={onClose}>
              <Icon />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sheet-foot">
          <HubLink />
          <span className="small muted">Ingelogd als <strong>{user.username}</strong> ({USER_ROLE_LABEL[user.role] ?? user.role})</span>
          <ThemaKeuze />
          <Button disabled={busy} onClick={onLogout}><LogOutIcon />Uitloggen</Button>
          <span className="small muted">Versie {APP_VERSION}</span>
        </div>
      </div>
    </div>
  );
}

export function Layout({ user }: { user: User }) {
  const { homeId, setHomeId } = useHome();
  const { offline } = useConnection();
  const { savedAt } = useSnapshotInfo();
  const location = useLocation();
  const meerActief = MORE_NAV.some((n) => location.pathname.startsWith(n.to));
  const homes = useQuery({ queryKey: qk.homes, queryFn: api.homes });
  const [moreOpen, setMoreOpen] = useState(false);
  const moreBtn = useRef<HTMLButtonElement>(null);

  // Archived homes are never picked by default; they can only be opened explicitly via the selector.
  const explicit = useRef(false);
  useEffect(() => {
    const list = homes.data;
    if (!list || !list.length) return;
    const cur = list.find((h) => h.id === homeId);
    if (!cur || (!cur.active && !explicit.current)) {
      const pick = list.find((h) => h.active) ?? list[0];
      if (pick.id !== homeId) setHomeId(pick.id);
    }
  }, [homes.data, homeId, setHomeId]);
  const current = homes.data?.find((h) => h.id === homeId);
  const activeHomes = homes.data?.filter((h) => h.active) ?? [];
  const archivedHomes = homes.data?.filter((h) => !h.active) ?? [];
  const hasHome = Boolean(homes.data?.length);

  useLiveUpdates(hasHome ? homeId : null);
  const logout = useLogout();

  // Move focus to the content after navigation (screen readers, keyboard users).
  const mainRef = useRef<HTMLElement>(null);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (window.location.hash) return;
    mainRef.current?.focus({ preventScroll: true });
    window.scrollTo?.(0, 0);
  }, [location.pathname]);

  // Scroll to an anchor (e.g. /systeem#adressen) once the page has rendered it.
  useEffect(() => {
    if (!location.hash) return;
    const id = decodeURIComponent(location.hash.slice(1));
    const t = setTimeout(() => document.getElementById(id)?.scrollIntoView?.({ block: 'start' }), 50);
    return () => clearTimeout(t);
  }, [location.pathname, location.hash]);

  const homeFree = HOME_FREE_PATHS.some((p) => location.pathname.startsWith(p));
  const closeMore = () => {
    setMoreOpen(false);
    moreBtn.current?.focus();
  };

  return (
    <div className={`shell${offline ? ' is-offline' : ''}${offline && savedAt ? ' from-snapshot' : ''}`}>
      <a className="skip-link" href="#main">Naar de inhoud</a>
      <nav className="sidebar" aria-label="Hoofdmenu">
        <div className="brand">
          <span className="brand-mark"><HomeIcon width={18} height={18} strokeWidth={2} /></span>
          Huis
        </div>
        <HubLink />
        {SIDEBAR_NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end} className={navClass}>
            <Icon />
            {label}
          </NavLink>
        ))}
        <div className="sidebar-foot">
          <span>Ingelogd als <strong>{user.username}</strong> ({USER_ROLE_LABEL[user.role] ?? user.role})</span>
          <ThemaKeuze />
          <div className="row" style={{ gap: 8 }}>
            <Button asChild size="klein"><Link to="/account">Account</Link></Button>
            <Button asChild size="klein"><Link to="/help">Help</Link></Button>
          </div>
          <Button disabled={logout.isPending} onClick={() => logout.mutate()}><LogOutIcon />Uitloggen</Button>
          <span className="small">Versie {APP_VERSION}</span>
        </div>
      </nav>

      <header className="mobile-top">
        <span className="brand"><span className="brand-mark"><HomeIcon width={16} height={16} strokeWidth={2} /></span>Huis</span>
        {current && <span className="small muted mobile-home">{current.name}</span>}
      </header>

      <main className="main" id="main" ref={mainRef} tabIndex={-1}>
        <OfflineBanner />
        {homes.data && homes.data.length > 1 && (
          <label className="row small muted">
            Woning
            <select className="input" value={homeId ?? ''} onChange={(e) => { explicit.current = true; setHomeId(e.target.value); }}>
              {activeHomes.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
              {archivedHomes.length > 0 && (
                <optgroup label="Gearchiveerd (alleen bekijken)">
                  {archivedHomes.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
                </optgroup>
              )}
            </select>
          </label>
        )}
        {current && !current.active && (
          <div className="note info" role="note">Je bekijkt een gearchiveerde woning ({current.name}). De geschiedenis is alleen-lezen.</div>
        )}
        {hasHome && homeId && <NextSteps homeId={homeId} />}
        {homes.data && homes.data.length === 0 && !homeFree ? <NoHomeSetup /> : <Outlet />}
        <footer className="app-foot small muted">Huis · versie {APP_VERSION}</footer>
      </main>

      <nav className="bottom-nav" aria-label="Hoofdmenu (mobiel)">
        {MAIN_NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end} className={({ isActive }) => `bottom-link${isActive ? ' active' : ''}`}>
            <Icon />
            <span>{label}</span>
          </NavLink>
        ))}
        <button
          ref={moreBtn}
          type="button"
          className={`bottom-link${meerActief ? ' active' : ''}`}
          // Peil 1.1.1 (E16, COMPONENTEN §2): het actieve item draagt aria-current, ook "Meer" op een pagina uit de
          // Meer-sheet; in forced colors onderstreept de basislaag precies dat item.
          aria-current={meerActief ? 'page' : undefined}
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen(true)}
        >
          <MenuIcon />
          <span>Meer</span>
        </button>
      </nav>
      {moreOpen && <MoreSheet user={user} onClose={closeMore} onLogout={() => logout.mutate()} busy={logout.isPending} />}
    </div>
  );
}
