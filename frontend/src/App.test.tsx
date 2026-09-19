/**
 * What the route table answers for an address it does not know.
 *
 * The catch-all used to be `<Navigate to="/" replace />`. The dashboard appeared, and
 * `replace` erased the address that had been asked for from the history, so a stale
 * bookmark, a renamed page and a typo were all answered as if they had been right.
 * `/integrations` is the one to type by accident: it is the word the sidebar uses for the
 * section whose route is `/providers`.
 *
 * `t()` returns the key here, so the assertions read as claims rather than as English.
 */

import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import { ThemeProvider } from '@/theme';

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn(() => Promise.resolve([])),
    post: vi.fn(() => Promise.resolve({})),
    put: vi.fn(() => Promise.resolve({})),
    delete: vi.fn(() => Promise.resolve({})),
  },
}));

const { AppRoutes } = await import('./App');

describe('the route table, asked for an address it does not know', () => {
  it('says so, and names the address back', async () => {
    renderWithProviders(
      <ThemeProvider>
        <AppRoutes />
      </ThemeProvider>,
      { route: '/integrations' },
    );

    expect(await screen.findByText('notfound.title')).toBeInTheDocument();
    expect(screen.getByText('/integrations')).toBeInTheDocument();
  });

  it('offers a way on rather than taking it', async () => {
    renderWithProviders(
      <ThemeProvider>
        <AppRoutes />
      </ThemeProvider>,
      { route: '/nope' },
    );

    const back = await screen.findByText('notfound.back');
    expect(back.closest('a')).toHaveAttribute('href', '/');
  });
});
