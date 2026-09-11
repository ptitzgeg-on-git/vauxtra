/**
 * The triage list may only say "all clear" about things it actually read.
 *
 * It is built from `/services` and `/providers`. When one of those does not come back, the
 * list is empty for a reason that has nothing to do with the panel being healthy -- and an
 * empty list rendered as a green tick is the panel vouching for endpoints it never saw.
 */

import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { Globe } from 'lucide-react';
import { renderWithProviders } from '@/test/render';
import { NeedsAttention, type AttentionItem } from './NeedsAttention';

const ITEM: AttentionItem = {
  id: 'svc-1',
  tone: 'danger',
  icon: <Globe />,
  title: 'api.example.com',
  to: '/services/1',
};

describe('NeedsAttention', () => {
  it('says all clear when both of its sources answered and neither had anything', () => {
    renderWithProviders(<NeedsAttention items={[]} loading={false} />);

    expect(screen.getByText('dashboard.attention.all_clear')).toBeInTheDocument();
    expect(screen.queryByText('dashboard.attention.partial')).not.toBeInTheDocument();
  });

  it('never says all clear when a source it triages did not come back', () => {
    renderWithProviders(<NeedsAttention items={[]} loading={false} incomplete />);

    expect(screen.queryByText('dashboard.attention.all_clear')).not.toBeInTheDocument();
    expect(screen.getByText('dashboard.attention.partial')).toBeInTheDocument();
    expect(screen.getByText('dashboard.attention.partial_body')).toBeInTheDocument();
  });

  it('keeps the warning above the rows when one source answered and the other did not', () => {
    // The half that arrived is still worth showing -- it is only the silence about the rest
    // that has to be said out loud.
    renderWithProviders(<NeedsAttention items={[ITEM]} loading={false} incomplete />);

    expect(screen.getByText('dashboard.attention.partial')).toBeInTheDocument();
    expect(screen.getByText('api.example.com')).toBeInTheDocument();
  });

  it('shows skeletons only while something is genuinely in flight', () => {
    const { container, rerender } = renderWithProviders(<NeedsAttention items={[]} loading />);
    expect(container.querySelectorAll('.animate-shimmer').length).toBeGreaterThan(0);

    // The defect this pins: `loading` used to stay true forever once a request had failed,
    // so the skeleton pulsed at somebody whose data was never coming.
    rerender(<NeedsAttention items={[]} loading={false} incomplete />);
    expect(container.querySelectorAll('.animate-shimmer')).toHaveLength(0);
  });
});
