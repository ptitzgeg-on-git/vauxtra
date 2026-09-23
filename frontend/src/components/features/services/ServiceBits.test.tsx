/**
 * The line a check leaves on a service row, for a service without a port.
 *
 * `POST /api/services/{id}/check` no longer probes port 0: it answers `tested: false`, the
 * status left at `unknown` and the name resolved all the same. Read like any other answer,
 * that printed "Unknown" on a service nothing had failed to reach, and kept quiet when the
 * name, the one thing the check did look at, did not resolve.
 *
 * `renderWithProviders` leaves `I18nProvider` out on purpose, so `t()` returns the key.
 */

import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import type { ServiceCheckResult } from '@/types/api';
import { CheckResultInline } from './ServiceBits';

const UNTESTED: ServiceCheckResult = {
  id: 3,
  status: 'unknown',
  latency_ms: null,
  dns_resolved: ['203.0.113.7'],
  tested: false,
};

describe('CheckResultInline, a service without a port', () => {
  it('says there is no port to test, not that the state is unknown', () => {
    renderWithProviders(<CheckResultInline result={UNTESTED} />);

    expect(screen.getByText('services.check.no_port')).toBeInTheDocument();
    expect(screen.queryByText('services.check.unknown')).toBeNull();
  });

  it('still shows what the name resolved to', () => {
    renderWithProviders(<CheckResultInline result={UNTESTED} />);
    expect(screen.getByText('203.0.113.7')).toBeInTheDocument();
  });

  it('says so when the name resolved to nothing', () => {
    renderWithProviders(<CheckResultInline result={{ ...UNTESTED, dns_resolved: [] }} />);
    expect(screen.getByText('services.check.no_resolution')).toBeInTheDocument();
  });

  it('reads the answer of an older instance as it always did', () => {
    // No `tested` field: the status is the whole answer.
    const answer: ServiceCheckResult = { id: 3, status: 'unknown', latency_ms: null, dns_resolved: null };
    renderWithProviders(<CheckResultInline result={answer} />);

    expect(screen.getByText('services.check.unknown')).toBeInTheDocument();
    expect(screen.queryByText('services.check.no_port')).toBeNull();
  });
});
