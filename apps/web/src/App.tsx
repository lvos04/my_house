import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { api, NetworkError } from './api/client';
import { ConnectionProvider, HomeProvider, PendingProvider } from './state';
import { UserContext } from './user';
import { Layout } from './components/Layout';
import { AuthPage } from './pages/Auth';
import { OverviewPage } from './pages/Overview';
import { ClimatePage } from './pages/Climate';
import { LightsPage } from './pages/Lights';
import { EnergyPage } from './pages/Energy';
import { DevicesPage } from './pages/Devices';
import { SystemPage } from './pages/System';
import { Loading } from './components/bits';
import { OfflineBanner } from './components/OfflineBanner';

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Keep last known data while offline; it is shown dimmed with a banner.
        staleTime: 10_000,
        gcTime: 24 * 60 * 60 * 1000,
        retry: (count, err) => err instanceof NetworkError && count < 2,
        refetchOnWindowFocus: true,
      },
      mutations: { retry: false },
    },
  });
}

function Gate() {
  const auth = useQuery({ queryKey: ['auth'], queryFn: api.authStatus, retry: 1 });
  if (auth.isLoading) return <div className="auth"><Loading /></div>;
  if (!auth.data)
    return (
      <div className="auth" style={{ flexDirection: 'column', gap: 16 }}>
        <OfflineBanner />
        <p className="muted">De server is niet bereikbaar. Controleer of de app op TrueNAS draait.</p>
        <button className="btn" type="button" onClick={() => auth.refetch()}>Opnieuw proberen</button>
      </div>
    );
  if (auth.data.setup_required) return <AuthPage mode="setup" />;
  if (!auth.data.user) return <AuthPage mode="login" />;
  const user = auth.data.user;
  return (
    <UserContext.Provider value={user}>
      <HomeProvider>
        <Routes>
          <Route element={<Layout user={user} />}>
            <Route index element={<OverviewPage />} />
            <Route path="klimaat" element={<ClimatePage />} />
            <Route path="verlichting" element={<LightsPage />} />
            <Route path="energie" element={<EnergyPage />} />
            <Route path="apparaten" element={<DevicesPage />} />
            <Route path="systeem" element={<SystemPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </HomeProvider>
    </UserContext.Provider>
  );
}

export function App({ client = makeQueryClient() }: { client?: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <PendingProvider>
          <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
            <Gate />
          </BrowserRouter>
        </PendingProvider>
      </ConnectionProvider>
    </QueryClientProvider>
  );
}
