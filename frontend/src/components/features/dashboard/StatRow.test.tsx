/**
 * A tile may print a number only when a request came back with one.
 *
 * Zero and "we could not ask" look identical as a figure, and only one of them means
 * everything is fine. Three of the six tiles used to count an unanswered request as a clean
 * nought; the hints under them went further and named a quantity nobody had measured.
 */

import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import { StatRow, type StatRowProps } from './StatRow';

const NOTHING_IN_FLIGHT: StatRowProps['loading'] = {
  services: false,
  providers: false,
  certificates: false,
  logs: false,
};

/** Everything answered, and every answer was genuinely zero. */
const ALL_ANSWERED: StatRowProps = {
  loading: NOTHING_IN_FLIGHT,
  services: { total: 0, enabled: 0, ok: 0, error: 0 },
  providers: { total: 0, enabled: 0, healthy: 0 },
  certificates: { expiring: 0, total: 0, thresholdDays: 30 },
  logs: { today: 0, todayCapped: false, total: 0 },
};

/** Nothing answered. The figures below are the zeros the tiles must refuse to print. */
const ALL_FAILED: StatRowProps = {
  loading: NOTHING_IN_FLIGHT,
  services: { total: 0, enabled: undefined, ok: 0, error: 0, failed: true },
  providers: { total: 0, enabled: 0, healthy: 0, failed: true },
  certificates: { expiring: 0, total: 0, thresholdDays: 30, failed: true },
  logs: { today: 0, todayCapped: false, total: undefined, failed: true },
};

describe('StatRow', () => {
  it('prints the zeros when the zeros are measurements', () => {
    renderWithProviders(<StatRow {...ALL_ANSWERED} />);

    expect(screen.getAllByText('0')).toHaveLength(6);
    expect(screen.queryByText('—')).not.toBeInTheDocument();
    expect(screen.queryByText('dashboard.stats.services_unknown')).not.toBeInTheDocument();
  });

  it('prints no number at all when no request came back', () => {
    renderWithProviders(<StatRow {...ALL_FAILED} />);

    expect(screen.getAllByText('—')).toHaveLength(6);
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('does not name a quantity in a hint when nobody answered', () => {
    renderWithProviders(<StatRow {...ALL_FAILED} />);

    // Three tiles are read off `/services`, so all three say the same thing.
    expect(screen.getAllByText('dashboard.stats.services_unknown')).toHaveLength(3);
    expect(screen.getByText('dashboard.stats.providers_unknown')).toBeInTheDocument();
    expect(screen.getByText('dashboard.stats.certificates_unknown')).toBeInTheDocument();
    expect(screen.getByText('dashboard.stats.logs_unknown')).toBeInTheDocument();
  });

  it('keeps a hint honest when the tile has its figure but the list behind the hint does not', () => {
    // The headline numbers fall back to `/stats`; `enabled` and `logs.total` have no such
    // fallback. A tile can therefore be perfectly well informed above a hint that is not.
    renderWithProviders(
      <StatRow
        {...ALL_ANSWERED}
        services={{ total: 12, enabled: undefined, ok: 11, error: 1 }}
        logs={{ today: 40, todayCapped: false, total: undefined }}
      />,
    );

    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('dashboard.stats.services_unknown')).toBeInTheDocument();
    expect(screen.getByText('dashboard.stats.logs_unknown')).toBeInTheDocument();
    expect(screen.queryByText('—')).not.toBeInTheDocument();
  });

  it('shows a skeleton only while a request is in flight, never after it failed', () => {
    const { container, rerender } = renderWithProviders(
      <StatRow {...ALL_FAILED} loading={{ ...NOTHING_IN_FLIGHT, services: true }} />,
    );
    expect(container.querySelectorAll('.animate-shimmer').length).toBeGreaterThan(0);

    rerender(<StatRow {...ALL_FAILED} />);
    expect(container.querySelectorAll('.animate-shimmer')).toHaveLength(0);
  });
});
