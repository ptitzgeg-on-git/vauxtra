import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query';
import { lazy, Suspense, useEffect } from 'react';
import { Toaster } from 'react-hot-toast';
import { Layout } from './components/layout/Layout';
import { ThemeProvider } from './theme';
import { RouteErrorBoundary } from './components/ui/ErrorBoundary';
import { api } from './api/client';
import { useT } from './i18n';
import type { AuthStatus } from './types/api';

const Dashboard = lazy(() => import('./pages/Dashboard').then((m) => ({ default: m.Dashboard })));
const Services = lazy(() => import('./pages/Services').then((m) => ({ default: m.Services })));
const Providers = lazy(() => import('./pages/Providers').then((m) => ({ default: m.Providers })));
const Settings = lazy(() => import('./pages/Settings').then((m) => ({ default: m.Settings })));
const Monitoring = lazy(() => import('./pages/Monitoring').then((m) => ({ default: m.Monitoring })));
const Certificates = lazy(() => import('./pages/Certificates').then((m) => ({ default: m.Certificates })));
const Templates = lazy(() => import('./pages/Templates').then((m) => ({ default: m.Templates })));
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

/**
 * Branded full-page loader for the two moments the app has nothing else to show: the
 * first auth check and a lazy route's chunk. Announced politely to screen readers.
 */
function FullPageLoader() {
  const t = useT();
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex min-h-screen flex-col items-center justify-center gap-5 bg-background text-muted-foreground animate-in fade-in motion-reduce:animate-none"
    >
      <div className="relative flex h-16 w-16 items-center justify-center">
        <span aria-hidden="true" className="absolute inset-0 rounded-full border-2 border-primary/15" />
        <span
          aria-hidden="true"
          className="absolute inset-0 rounded-full border-2 border-transparent border-t-primary motion-safe:animate-spin"
        />
        <img src="/favicon.svg" alt="" width={28} height={28} className="h-7 w-7" />
      </div>
      <p className="text-sm font-medium">{t('ui.loading')}</p>
    </div>
  );
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
    return <FullPageLoader />;
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
  const t = useT();

  return (
    <Suspense fallback={<FullPageLoader />}>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<RouteErrorBoundary page={t('nav.dashboard')}><Dashboard /></RouteErrorBoundary>} />
          <Route path="services" element={<RouteErrorBoundary page={t('nav.services')}><Services /></RouteErrorBoundary>} />
          <Route path="templates" element={<RouteErrorBoundary page={t('nav.templates')}><Templates /></RouteErrorBoundary>} />
          <Route path="providers" element={<RouteErrorBoundary page={t('nav.providers')}><Providers /></RouteErrorBoundary>} />
          <Route path="monitoring" element={<RouteErrorBoundary page={t('nav.monitoring')}><Monitoring /></RouteErrorBoundary>} />
          <Route path="settings" element={<RouteErrorBoundary page={t('nav.settings')}><Settings /></RouteErrorBoundary>} />
          <Route path="certificates" element={<RouteErrorBoundary page={t('nav.certificates')}><Certificates /></RouteErrorBoundary>} />
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
            gutter={10}
            toastOptions={{
              duration: 4000,
              style: {
                background: 'rgb(var(--vx-popover, var(--vx-card)))',
                color: 'rgb(var(--vx-popover-fg, var(--vx-fg)))',
                border: '1px solid rgb(var(--vx-border))',
                boxShadow:
                  'var(--vx-shadow-elevated, 0 12px 32px -12px rgba(0, 0, 0, 0.28), 0 2px 6px -1px rgba(0, 0, 0, 0.08))',
                borderRadius: '12px',
                fontSize: '14px',
                fontWeight: 500,
                lineHeight: '1.4',
                padding: '10px 14px',
                maxWidth: '420px',
              },
              success: {
                iconTheme: {
                  primary: 'rgb(var(--vx-success, 22 163 74))',
                  secondary: 'rgb(var(--vx-success-fg, 255 255 255))',
                },
              },
              error: {
                iconTheme: {
                  primary: 'rgb(var(--vx-destructive))',
                  secondary: 'rgb(var(--vx-destructive-fg, 255 255 255))',
                },
              },
              loading: {
                // A top-level duration would otherwise dismiss a toast.promise() mid-flight.
                duration: Infinity,
                iconTheme: {
                  primary: 'rgb(var(--vx-primary))',
                  secondary: 'rgb(var(--vx-muted))',
                },
              },
            }}
          />
          <AuthGate />
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

export default App;
