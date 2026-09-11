/**
 * `render()` with the two providers every screen in this app sits under.
 *
 * `I18nProvider` is deliberately NOT one of them. Without it `useT()` falls back to the
 * context default, which returns the key -- so an assertion reads
 * `dashboard.attention.all_clear` rather than "All clear — every endpoint, integration and
 * certificate is healthy". That is the claim the test is about; the English sentence is a
 * translation of it, and rewording one should not turn a test red.
 */

import type { ReactElement, ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderOptions, type RenderResult } from '@testing-library/react';

/** No retries and no refetching: a test waits for the component, never for a timer. */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
}

export interface RenderWithProvidersResult extends RenderResult {
  queryClient: QueryClient;
}

export function renderWithProviders(
  ui: ReactElement,
  options: Omit<RenderOptions, 'wrapper'> & { route?: string; queryClient?: QueryClient } = {},
): RenderWithProvidersResult {
  const { route = '/', queryClient = makeQueryClient(), ...rest } = options;

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  }

  return { ...render(ui, { wrapper: Wrapper, ...rest }), queryClient };
}
