import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query';
import { lazy, Suspense, useEffect } from 'react';
import { Toaster } from 'react-hot-toast';
import { Layout } from './components/layout/Layout';
import { ThemeProvider } from './theme';
import { ErrorBoundary } from './components/ui/ErrorBoundary';
import { api } from './api/client';

const Dashboard = lazy(() => import('./pages/Dashboard').then((m) => ({ default: m.Dashboard })));
const Services = lazy(() => import('./pages/Services').then((m) => ({ default: m.Services })));
const Providers = lazy(() => import('./pages/Providers').then((m) => ({ default: m.Providers })));
const Settings = lazy(() => import('./pages/Settings').then((m) => ({ default: m.Settings })));
const Monitoring = lazy(() => import('./pages/Monitoring').then((m) => ({ default: m.Monitoring })));
const Certificates = lazy(() => import('./pages/Certificates').then((m) => ({ default: m.Certificates })));
const Login = lazy(() => import('./pages/Login').then((m) => ({ default: m.Login })));
const Setup = lazy(() => import('./pages/Setup').then((m) => ({ default: m.Setup })));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

interface AuthStatus {
  authenticated: boolean;
  auth_required: boolean;
  setup_required: boolean;
}

function AuthGate() {
  const qc = useQueryClient();
  const { data: auth, isLoading } = useQuery<AuthStatus>({
    queryKey: ['auth-status'],
    queryFn: () => api.get<AuthStatus>('/auth/me'),
    staleTime: 60_000,
    retry: false,
  });

  // Listen for 401 events from Axios interceptor
  useEffect(() => {
    const handler = () => qc.invalidateQueries({ queryKey: ['auth-status'] });
    window.addEventListener('vauxtra:auth-expired', handler);
    return () => window.removeEventListener('vauxtra:auth-expired', handler);
  }, [qc]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background text-muted-foreground animate-pulse">
        Loading…
      </div>
    );
  }

  // Show login if password is required and not authenticated
  if (auth?.auth_required && !auth?.authenticated) {
    return <Login onSuccess={() => qc.invalidateQueries({ queryKey: ['auth-status'] })} />;
  }

  // Show setup wizard if server says setup is required
  if (auth?.setup_required) {
    return (
      <Setup
        onComplete={async () => {
          // Mark setup as complete on server
          await api.post('/auth/setup-complete');
          // Invalidate all queries that may have been created during setup
          qc.invalidateQueries({ queryKey: ['auth-status'] });
          qc.invalidateQueries({ queryKey: ['providers'] });
          qc.invalidateQueries({ queryKey: ['services'] });
          qc.invalidateQueries({ queryKey: ['tags'] });
          qc.invalidateQueries({ queryKey: ['environments'] });
          qc.invalidateQueries({ queryKey: ['webhooks'] });
          qc.invalidateQueries({ queryKey: ['docker-endpoints'] });
        }}
      />
    );
  }

  return <AppRoutes />;
}

function AppRoutes() {
  const pageFallback = (
    <div className="min-h-screen flex items-center justify-center bg-background text-muted-foreground animate-pulse">
      Loading…
    </div>
  );

  return (
    <Suspense fallback={pageFallback}>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<ErrorBoundary fallbackTitle="Dashboard unavailable"><Dashboard /></ErrorBoundary>} />
          <Route path="services" element={<ErrorBoundary fallbackTitle="Services unavailable"><Services /></ErrorBoundary>} />
          <Route path="providers" element={<ErrorBoundary fallbackTitle="Providers unavailable"><Providers /></ErrorBoundary>} />
          <Route path="monitoring" element={<ErrorBoundary fallbackTitle="Monitoring unavailable"><Monitoring /></ErrorBoundary>} />
          <Route path="settings" element={<ErrorBoundary fallbackTitle="Settings unavailable"><Settings /></ErrorBoundary>} />
          <Route path="certificates" element={<ErrorBoundary fallbackTitle="Certificates unavailable"><Certificates /></ErrorBoundary>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  );
}

function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Toaster 
            position="bottom-right"
            toastOptions={{
               style: {
                  background: 'rgb(var(--vx-card))',
                  color: 'rgb(var(--vx-fg))',
                  border: '1px solid rgb(var(--vx-border))',
                 boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)',
                 fontSize: '14px',
                 fontWeight: 500,
                 borderRadius: '12px'
               }
            }}
          />
          <AuthGate />
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

export default App;
