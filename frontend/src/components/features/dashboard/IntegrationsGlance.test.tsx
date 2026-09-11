/**
 * "No integrations yet" and "the list did not load" are the same empty array.
 *
 * Only one of them should be invited to connect a first one. The other has to say what
 * happened and offer to try again -- an operator who already has six integrations and is
 * told to add their first has been told something false about their own panel.
 */

import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { IntegrationsGlance } from './IntegrationsGlance';
import type { Provider } from '@/types/api';

const PROVIDER: Provider = {
  id: 1,
  name: 'npm-lan',
  type: 'nginx_proxy_manager',
  url: 'http://127.0.0.1:81',
  username: 'admin',
  enabled: true,
  extra: {},
  created_at: '2026-01-01T00:00:00Z',
};

const BASE = {
  loading: false,
  health: undefined,
  healthError: false,
  types: undefined,
  error: false,
  onRetry: () => {},
  onAddProvider: () => {},
};

describe('IntegrationsGlance', () => {
  it('invites a first integration when the list answered and was empty', () => {
    renderWithProviders(<IntegrationsGlance {...BASE} providers={[]} />);

    expect(screen.getByText('dashboard.integrations.empty_title')).toBeInTheDocument();
    expect(screen.queryByText('dashboard.integrations.error')).not.toBeInTheDocument();
  });

  it('says what happened, and does not invite anything, when the list failed', () => {
    renderWithProviders(<IntegrationsGlance {...BASE} providers={undefined} error />);

    expect(screen.getByText('dashboard.integrations.error')).toBeInTheDocument();
    expect(screen.queryByText('dashboard.integrations.empty_title')).not.toBeInTheDocument();
  });

  it('offers a retry that actually retries', async () => {
    const onRetry = vi.fn();
    renderWithProviders(<IntegrationsGlance {...BASE} providers={undefined} error onRetry={onRetry} />);

    await userEvent.click(screen.getByRole('button', { name: 'ui.error.retry' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('shows the integrations it has, failure or not', () => {
    renderWithProviders(<IntegrationsGlance {...BASE} providers={[PROVIDER]} />);

    expect(screen.getByText('npm-lan')).toBeInTheDocument();
    expect(screen.queryByText('dashboard.integrations.empty_title')).not.toBeInTheDocument();
  });

  it('stops pulsing once the request has answered, including by failing', () => {
    const { container, rerender } = renderWithProviders(
      <IntegrationsGlance {...BASE} providers={undefined} loading />,
    );
    expect(container.querySelectorAll('.animate-shimmer').length).toBeGreaterThan(0);

    rerender(<IntegrationsGlance {...BASE} providers={undefined} error />);
    expect(container.querySelectorAll('.animate-shimmer')).toHaveLength(0);
  });
});
