/**
 * A drift report that holds warnings only, on the screens.
 *
 * `ok` in a drift answer means "no error": it is the line auto-reconcile acts on, and a
 * warning does not cross it. The drawer, its badges and the row indicator all read `ok` as
 * "in sync", so a report listing a warning was headed "Everything is in sync", said the
 * providers served the host "exactly as configured", and kept Reconcile off. Two of those
 * warnings are what Reconcile fixes: a DNS answer and a proxy origin that differ.
 *
 * The drift check now also reads the DNS integrations the service is not published to
 * (`dns_answered_elsewhere`, `dns_elsewhere_check_failed`), which made the old reading
 * common: every split-horizon name carries one. Reconcile writes to none of those, so it has
 * nothing to offer about them and stays off when they are all the report holds.
 *
 * `renderWithProviders` leaves `I18nProvider` out on purpose, so `t()` returns the key. One
 * test mounts the real provider, to read the sentence an operator reads.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { I18nProvider } from '@/i18n';
import { renderWithProviders } from '@/test/render';
import type { DriftIssue, DriftResult, Service } from '@/types/api';
import { DriftDrawer, type DriftDrawerProps } from './DriftDrawer';
import { driftStanding } from './helpers';
import { DriftIndicator } from './ServiceBits';

const SERVICE: Service = {
  id: 4,
  subdomain: 'jellyfin',
  domain: 'example.test',
  target_ip: '10.0.0.2',
  target_port: 8096,
  forward_scheme: 'http',
  websocket: false,
  expose_mode: 'proxy_dns',
  public_target_mode: 'manual',
  auto_update_dns: false,
  tunnel_hostname: '',
  dns_ip: '10.0.0.2',
  npm_host_id: null,
  dns_provider_id: 1,
  proxy_provider_id: 2,
  tunnel_provider_id: null,
  enabled: true,
  status: 'ok',
  last_checked: '2026-09-22 12:00:00',
  created_at: '2026-09-22 09:00:00',
  tags: [],
  environments: [],
};

const ANSWERED_ELSEWHERE: DriftIssue = {
  severity: 'warn',
  type: 'dns_answered_elsewhere',
  provider: 'Cloudflare',
  detail: 'jellyfin.example.test also resolves to 203.0.113.7 here, while the service publishes 10.0.0.2',
  detail_key: 'answered_elsewhere',
  detail_params: { host: 'jellyfin.example.test', found: '203.0.113.7', expected: '10.0.0.2' },
};

const UNREADABLE_ELSEWHERE: DriftIssue = {
  severity: 'warn',
  type: 'dns_elsewhere_check_failed',
  provider: 'Pi-hole',
  detail: 'connection refused',
};

const TARGET_DIFFERS: DriftIssue = {
  severity: 'warn',
  type: 'dns_target_mismatch',
  provider: 'AdGuard',
  detail: 'Expected 10.0.0.2, found 10.0.0.9',
  detail_key: 'answer_mismatch',
  detail_params: { expected: '10.0.0.2', found: '10.0.0.9' },
};

const RECORD_MISSING: DriftIssue = {
  severity: 'error',
  type: 'missing_dns_rewrite',
  provider: 'AdGuard',
  detail: 'The jellyfin.example.test record is missing on this integration.',
};

/** `ok` as the API writes it: no issue of severity "error". */
function report(...issues: DriftIssue[]): DriftResult {
  return {
    service_id: SERVICE.id,
    public_host: 'jellyfin.example.test',
    mode: 'proxy_dns',
    ok: !issues.some((issue) => issue.severity === 'error'),
    issues,
  };
}

function renderDrawer(drift: DriftResult, overrides: Partial<DriftDrawerProps> = {}) {
  return renderWithProviders(
    <DriftDrawer
      open
      onClose={() => {}}
      service={SERVICE}
      drift={drift}
      isChecking={false}
      checkError={null}
      onRecheck={() => {}}
      isReconciling={false}
      onReconcile={() => {}}
      reconcileResult={undefined}
      {...overrides}
    />,
  );
}

const reconcileButton = () => screen.getByRole('button', { name: 'services.drift.reconcile' });

describe('driftStanding', () => {
  it('counts nothing on an empty report, and leaves Reconcile nothing to push', () => {
    expect(driftStanding(report())).toEqual({ errors: 0, warnings: 0, reconcilable: false });
  });

  it('gives Reconcile a DNS answer that differs, although the report is `ok`', () => {
    const drift = report(TARGET_DIFFERS);

    expect(drift.ok).toBe(true);
    expect(driftStanding(drift)).toEqual({ errors: 0, warnings: 1, reconcilable: true });
  });

  it('keeps what other integrations answer out of Reconcile', () => {
    expect(driftStanding(report(ANSWERED_ELSEWHERE, UNREADABLE_ELSEWHERE))).toEqual({
      errors: 0,
      warnings: 2,
      reconcilable: false,
    });
  });

  it('still hands Reconcile an error sitting next to them', () => {
    expect(driftStanding(report(RECORD_MISSING, ANSWERED_ELSEWHERE))).toEqual({
      errors: 1,
      warnings: 1,
      reconcilable: true,
    });
  });
});

describe('DriftDrawer, a report with warnings only', () => {
  it('does not say everything is in sync above a warning', () => {
    renderDrawer(report(ANSWERED_ELSEWHERE));

    expect(screen.queryByText('services.drift.in_sync_title')).toBeNull();
    expect(screen.queryByText('services.drift.in_sync')).toBeNull();
    expect(screen.getByText('services.drift.out_of_sync_title')).toBeInTheDocument();
    expect(screen.getByText('services.drift.warnings')).toBeInTheDocument();
  });

  it('names the two new issue types, rather than printing their code', () => {
    renderDrawer(report(ANSWERED_ELSEWHERE, UNREADABLE_ELSEWHERE));

    expect(screen.getByText('services.drift.type.dns_answered_elsewhere')).toBeInTheDocument();
    expect(screen.getByText('services.drift.type.dns_elsewhere_check_failed')).toBeInTheDocument();
    expect(screen.queryByText('dns_answered_elsewhere')).toBeNull();
  });

  it('keeps Reconcile off, and says why, when nothing reported is its to push', () => {
    renderDrawer(report(ANSWERED_ELSEWHERE, UNREADABLE_ELSEWHERE));

    expect(reconcileButton()).toBeDisabled();
    expect(screen.getByText('services.drift.nothing_to_push_body')).toBeInTheDocument();
    expect(screen.queryByText('services.drift.out_of_sync_body')).toBeNull();
  });

  it('offers Reconcile for a DNS answer that differs', () => {
    renderDrawer(report(TARGET_DIFFERS));

    expect(reconcileButton()).toBeEnabled();
    expect(screen.getByText('services.drift.out_of_sync_body')).toBeInTheDocument();
  });

  it('offers Reconcile for an error, as it always did, next to a warning it cannot clear', () => {
    renderDrawer(report(RECORD_MISSING, ANSWERED_ELSEWHERE));

    expect(reconcileButton()).toBeEnabled();
  });

  it('is in sync, with Reconcile off, when the report holds nothing', () => {
    renderDrawer(report());

    expect(screen.getByText('services.drift.in_sync_title')).toBeInTheDocument();
    expect(reconcileButton()).toBeDisabled();
  });

  it('keeps Reconcile off while a check is running', () => {
    renderDrawer(report(TARGET_DIFFERS), { isChecking: true });

    expect(reconcileButton()).toBeDisabled();
  });

  it('shows the warning Reconcile left in place after it, not "in sync"', () => {
    renderDrawer(report(ANSWERED_ELSEWHERE), {
      reconcileResult: {
        ok: true,
        before: report(TARGET_DIFFERS, ANSWERED_ELSEWHERE),
        push: { ok: true, errors: [] },
        after: report(ANSWERED_ELSEWHERE),
      },
    });

    expect(screen.getByText('services.drift.reconciled_title')).toBeInTheDocument();
    expect(screen.queryByText('services.drift.in_sync')).toBeNull();
    expect(screen.getByText('services.drift.remaining_issues')).toBeInTheDocument();
  });
});

describe('DriftDrawer, in French', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('writes what the other integration answers, and what the service publishes', async () => {
    localStorage.setItem('vauxtra_lang', 'fr');
    renderWithProviders(
      <I18nProvider>
        <DriftDrawer
          open
          onClose={() => {}}
          service={SERVICE}
          drift={report(ANSWERED_ELSEWHERE)}
          isChecking={false}
          checkError={null}
          onRecheck={() => {}}
          isReconciling={false}
          onReconcile={() => {}}
          reconcileResult={undefined}
        />
      </I18nProvider>,
    );

    expect(
      await screen.findByText(
        /^jellyfin\.example\.test est aussi résolu ici, vers 203\.0\.113\.7, alors que le service publie 10\.0\.0\.2\./,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('Aussi résolu par une autre intégration')).toBeInTheDocument();
  });
});

describe('DriftIndicator', () => {
  const badge = (drift: DriftResult) => {
    renderWithProviders(<DriftIndicator drift={drift} onOpen={() => {}} />);
    return screen.getByRole('button');
  };

  it('does not read a report with warnings only as in sync', () => {
    expect(badge(report(ANSWERED_ELSEWHERE))).toHaveTextContent('services.drift.warnings');
  });

  it('reads an empty report as in sync', () => {
    expect(badge(report())).toHaveTextContent('services.drift.in_sync');
  });

  it('puts the errors first when there are both', () => {
    expect(badge(report(RECORD_MISSING, ANSWERED_ELSEWHERE))).toHaveTextContent('services.drift.errors');
  });
});
