import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { Toaster } from 'react-hot-toast';
import { Layout } from './components/layout/Layout';
import { Dashboard } from './pages/Dashboard';
import { Services } from './pages/Services';
import { Providers } from './pages/Providers';
import { Settings } from './pages/Settings';
import { Monitoring } from './pages/Monitoring';
import { Certificates } from './pages/Certificates';
import { Login } from './pages/Login';
import { Setup } from './pages/Setup';
import { ThemeProvider } from './theme';
import { ErrorBoundary } from './components/ui/ErrorBoundary';
import { api } from './api/client';

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

  // Show login if password is required and not authenticated
  if (auth?.auth_required && !auth?.authenticated) {
    return <Login onSuccess={() => qc.invalidateQueries({ queryKey: ['auth-status'] })} />;
  }

  return <AppRoutes />;
}

function AppRoutes() {
  return (
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
