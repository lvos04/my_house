import { NavLink, Outlet } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { useConnection, useHome } from '../state';
import { useLiveUpdates } from '../ws';
import { OfflineBanner } from './OfflineBanner';
import { BoltIcon, BulbIcon, DeviceIcon, GearIcon, GridIcon, HomeIcon, ThermoIcon } from './Icons';
import { useEffect } from 'react';
import type { User } from '../api/types';

const NAV = [
  { to: '/', label: 'Overzicht', icon: GridIcon, end: true },
  { to: '/klimaat', label: 'Klimaat', icon: ThermoIcon },
  { to: '/verlichting', label: 'Verlichting', icon: BulbIcon },
  { to: '/energie', label: 'Energie', icon: BoltIcon },
  { to: '/apparaten', label: 'Apparaten', icon: DeviceIcon },
  { to: '/systeem', label: 'Systeem', icon: GearIcon },
];

export function Layout({ user }: { user: User }) {
  const { homeId, setHomeId } = useHome();
  const { offline } = useConnection();
  const qc = useQueryClient();
  const homes = useQuery({ queryKey: ['homes'], queryFn: api.homes });

  useEffect(() => {
    const list = homes.data;
    if (list && list.length && (!homeId || !list.some((h) => h.id === homeId))) {
      setHomeId((list.find((h) => h.active) ?? list[0]).id);
    }
  }, [homes.data, homeId, setHomeId]);

  useLiveUpdates(homeId);

  const logout = useMutation({
    mutationFn: api.logout,
    onSettled: () => qc.invalidateQueries({ queryKey: ['auth'] }),
  });

  return (
    <div className={`shell${offline ? ' is-offline' : ''}`}>
      <nav className="sidebar" aria-label="Hoofdmenu">
        <div className="brand">
          <span className="brand-mark"><HomeIcon width={18} height={18} strokeWidth={2} /></span>
          Huis
        </div>
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
            <Icon />
            {label}
          </NavLink>
        ))}
        <div className="sidebar-foot">
          <span>Ingelogd als <strong>{user.username}</strong> ({user.role === 'admin' ? 'beheerder' : 'kijker'})</span>
          <button type="button" className="btn" onClick={() => logout.mutate()}>Uitloggen</button>
        </div>
      </nav>
      <main className="main">
        <OfflineBanner />
        {homes.data && homes.data.length > 1 && (
          <label className="row small muted">
            Woning
            <select className="input" value={homeId ?? ''} onChange={(e) => setHomeId(e.target.value)}>
              {homes.data.map((h) => (
                <option key={h.id} value={h.id}>{h.name}{h.active ? '' : ' (archief)'}</option>
              ))}
            </select>
          </label>
        )}
        {homes.data && homes.data.length === 0 ? (
          <div className="card"><h2>Nog geen woning</h2><p className="muted">Maak eerst een woning aan via Systeem › Installatie.</p></div>
        ) : (
          <Outlet />
        )}
      </main>
    </div>
  );
}
