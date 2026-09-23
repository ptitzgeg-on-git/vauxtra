/**
 * The card says what the last test found, and a check that was not run is not a finding.
 *
 * A Cloudflare tunnel skips two checks on every routine test: the write probe (safe mode) and
 * the zone lookup (no hostname was given). The card counted both as warnings and titled a
 * tunnel with every read granted "Passed with 2 warnings", in amber, with the two lines under
 * it; nothing the operator could change would ever clear them. It now says the test passed
 * and that two checks were not run, and lists them in grey.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { renderWithProviders } from '@/test/render';
import type { Provider, ProviderValidationCheck } from '@/types/api';

import { ProviderCard } from './ProviderCard';
import type { ProviderDiagnostics } from './providerHealth';

const TUNNEL: Provider = {
  id: 7,
  name: 'Tunnel at the edge',
  type: 'cloudflare_tunnel',
  url: '',
  username: 'account',
  enabled: true,
  extra: {},
  created_at: '2026-01-01T00:00:00Z',
};

const PASSED: ProviderValidationCheck[] = [
  { name: 'token_verify', ok: true, blocking: true, detail: 'API token is active' },
  { name: 'tunnel_config_read', ok: true, blocking: true, detail: 'Can read tunnel configuration' },
];
const WRITE_NOT_RUN: ProviderValidationCheck = {
  name: 'tunnel_config_write',
  ok: false,
  blocking: false,
  skipped: true,
  detail: 'Write probe skipped (safe mode).',
  detail_code: 'tunnel_config_write_skipped',
};
const NO_HOSTNAME: ProviderValidationCheck = {
  name: 'zone_lookup',
  ok: false,
  blocking: false,
  skipped: true,
  detail: 'No hostname hint provided for DNS scope checks',
  detail_code: 'zone_lookup_no_hint',
};

function renderCard(checks: ProviderValidationCheck[]) {
  const diagnostics: ProviderDiagnostics = { ok: true, testedAt: Date.now(), validation: { checks, warnings: [] } };
  return renderWithProviders(
    <ProviderCard
      provider={TUNNEL}
      health={{ score: 100, severity: 'ok' }}
      status={{ labelKey: 'providers.status.active', tone: 'success' }}
      diagnostics={diagnostics}
      onTest={vi.fn()}
      onValidate={vi.fn()}
      onInspect={vi.fn()}
      onEdit={vi.fn()}
      onDelete={vi.fn()}
      onToggleEnabled={vi.fn()}
    />,
  );
}

describe('the verdict on a tunnel with every read granted', () => {
  it('says it passed and that two checks were not run, not that it has warnings', () => {
    renderCard([...PASSED, WRITE_NOT_RUN, NO_HOSTNAME]);
    expect(screen.getByText('providers.diag.passed_skipped')).toBeInTheDocument();
    expect(screen.queryByText('providers.diag.passed_warnings')).not.toBeInTheDocument();
  });

  it('lists what was not run in grey, not in amber', () => {
    renderCard([...PASSED, WRITE_NOT_RUN, NO_HOSTNAME]);
    for (const check of [WRITE_NOT_RUN, NO_HOSTNAME]) {
      const line = screen.getByText(`– ${check.detail}`);
      expect(line).toHaveClass('text-muted-foreground');
      expect(line).not.toHaveClass('text-warning');
    }
  });

  it('still counts a check that ran and failed as a warning', () => {
    const lookupFailed: ProviderValidationCheck = {
      name: 'zone_lookup',
      ok: false,
      blocking: false,
      detail: 'Cannot resolve DNS zone for hostname',
      detail_code: 'zone_lookup_failed',
    };
    renderCard([...PASSED, WRITE_NOT_RUN, lookupFailed]);
    expect(screen.getByText('providers.diag.passed_warnings')).toBeInTheDocument();
  });

  it('keeps the plain verdict when every check ran', () => {
    renderCard(PASSED);
    expect(screen.getByText('providers.diag.passed')).toBeInTheDocument();
  });
});
