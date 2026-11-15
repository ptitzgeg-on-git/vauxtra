/**
 * The connector card printed the backend's reason as it came: `missing_account_id`, a code,
 * under the name of an integration that only needed its account ID filled in. The providers
 * page already translated it through `tunnelReasonLabel`; this card did not.
 */

import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import type { TunnelHealthResponse } from '@/types/api';
import { TunnelsCard } from './TunnelsCard';

function renderCard(data: TunnelHealthResponse) {
  return renderWithProviders(
    <TunnelsCard data={data} loading={false} isError={false} refreshing={false} onRefresh={() => {}} />,
  );
}

describe('TunnelsCard', () => {
  it('says in words why a connector cannot be read', () => {
    renderCard({
      items: [{ id: 3, name: 'Maison', tunnel_id: 't-1', health: { ok: false, status: 'down', reason: 'missing_account_id' } }],
    });
    expect(screen.getByText('providers.tunnel.reason.missing_account_id')).toBeInTheDocument();
    expect(screen.queryByText('missing_account_id')).not.toBeInTheDocument();
  });

  it('keeps a reason it has no sentence for, rather than hiding it', () => {
    renderCard({
      items: [{ id: 3, name: 'Maison', tunnel_id: 't-1', health: { ok: false, status: 'degraded', reason: 'one connector of two' } }],
    });
    expect(screen.getByText('one connector of two')).toBeInTheDocument();
  });

  it('shows an error the provider spelled out instead of the reason', () => {
    renderCard({
      items: [{ id: 3, name: 'Maison', health: { ok: false, error: 'HTTP 403', reason: 'missing_account_id' } }],
    });
    expect(screen.getByText('HTTP 403')).toBeInTheDocument();
    expect(screen.queryByText('providers.tunnel.reason.missing_account_id')).not.toBeInTheDocument();
  });
});
