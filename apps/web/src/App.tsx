import { useEffect, useMemo, useState } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, isTransientError, onConnectionEvent } from './api/client';
import { qk } from './api/keys';
import type { User } from './api/types';
import { ConnectionProvider, HomeProvider, PendingProvider, useConnection } from './state';
import { SnapshotProvider } from './snapshot';
import { UserContext } from './user';
import { Layout } from './components/Layout';
import { AuthPage } from './pages/Auth';
import { OverviewPage } from './pages/Overview';
import { ClimatePage } from './pages/Climate';
import { LightsPage } from './pages/Lights';
import { EnergyPage } from './pages/Energy';
import { DevicesPage } from './pages/Devices';
import { OptimizationPage } from './pages/Optimization';
import { SystemPage } from './pages/System';
import { AccountPage } from './pages/Account';
import { InstallPage } from './pages/Install';
import { HelpPage } from './pages/Help';
import { Loading } from './components/bits';
import { OfflineBanner } from './components/OfflineBanner';
import { Button } from './components/ui/button';
import { CloudOffIcon, RetryIcon } from './components/Icons';

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Keep last known data while offline; it is shown dimmed with a banner.
        staleTime: 10_000,
        gcTime: 24 * 60 * 60 * 1000,
        retry: (count, err) => isTransientError(err) && count < 2,
        refetchOnWindowFocus: true,
      },
      mutations: { retry: false },
    },
  });
}

export function AppRoutes({ user }: { user: User }) {
  return (
    <Routes>
      <Route element={<Layout user={user} />}>
        <Route index element={<OverviewPage />} />
        <Route path="klimaat" element={<ClimatePage />} />
        <Route path="verlichting" element={<LightsPage />} />
        <Route path="energie" element={<EnergyPage />} />
        <Route path="optimalisatie" element={<OptimizationPage />} />
        <Route path="apparaten" element={<DevicesPage />} />
        <Route path="systeem" element={<SystemPage />} />
        <Route path="account" element={<AccountPage />} />
        <Route path="installatie" element={<InstallPage />} />
        <Route path="help" element={<HelpPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

function Gate() {
  const qc = useQueryClient();
  const { starting } = useConnection();
  const auth = useQuery({ queryKey: qk.auth, queryFn: api.authStatus, retry: 1 });
  const user = auth.data?.user ?? null;
  const me = useQuery({ queryKey: qk.me, queryFn: api.me, enabled: Boolean(user), staleTime: 5 * 60_000, retry: false });

  // A request answered 401: the session ended elsewhere (logout, password change, expiry). Check again.
  useEffect(
    () =>
      onConnectionEvent((ev) => {
        if (ev.type === 'auth_required') qc.invalidateQueries({ queryKey: qk.auth });
      }),
    [qc],
  );

  const merged = useMemo<User | null>(() => {
    if (!user) return null;
    const m = me.data;
    return m && typeof m === 'object' && m.id === user.id ? { ...user, ...m } : user;
  }, [user, me.data]);

  if (auth.isLoading) return <div className="auth"><Loading /></div>;
  if (!auth.data)
    return (
      <div className="auth" style={{ flexDirection: 'column', gap: 16 }}>
        <OfflineBanner />
        {/* Foutpaneel op paginaniveau in de onbekend-stijl: een bron die weg is, is geen gemeten falen. */}
        <div className="card server-weg" style={{ maxWidth: 420, width: '100%' }}>
          <h1 className="inline-flex items-center gap-2" style={{ fontSize: 20 }}><CloudOffIcon />De server is niet bereikbaar</h1>
          <p className="muted" style={{ margin: 0 }}>{starting ?? 'De server is niet bereikbaar. Controleer of de app op de NAS draait.'}</p>
          <Button className="self-start" onClick={() => auth.refetch()}><RetryIcon />Opnieuw proberen</Button>
        </div>
      </div>
    );
  if (auth.data.setup_required) return <AuthPage mode="setup" />;
  if (!merged) return <AuthPage mode="login" />;
  return (
    <UserContext.Provider value={merged}>
      <HomeProvider>
        <AppRoutes user={merged} />
      </HomeProvider>
    </UserContext.Provider>
  );
}

export function App({ client }: { client?: QueryClient }) {
  const [qc] = useState(() => client ?? makeQueryClient());
  return (
    <QueryClientProvider client={qc}>
      <ConnectionProvider>
        <SnapshotProvider client={qc}>
          <PendingProvider>
            <BrowserRouter>
              <Gate />
            </BrowserRouter>
          </PendingProvider>
        </SnapshotProvider>
      </ConnectionProvider>
    </QueryClientProvider>
  );
}
