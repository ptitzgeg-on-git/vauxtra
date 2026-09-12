import { useNavigate } from 'react-router-dom';
import { Activity, AlertTriangle, Globe, Plug, ScrollText, ShieldCheck } from 'lucide-react';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { StatCard } from '@/components/ui';

export interface StatRowProps {
  /** True only while a tile’s request is still in flight. Once it has answered, see `failed`. */
  loading: {
    services: boolean;
    providers: boolean;
    certificates: boolean;
    logs: boolean;
  };
  // `failed` is what stops a tile reporting a confident zero when the request behind it did
  // not come back. Zero and "we could not ask" look identical as a number, and only one of
  // them means everything is fine. The certificate tile was given this from the start; the
  // other three either counted an unanswered request as a clean nought, or — worse — stayed
  // `loading` forever, pulsing a skeleton at somebody whose data was never coming.
  // `enabled` and `total` below are `undefined` rather than `0` when the list they are counted
  // from did not come back — which can happen while the tile’s own figure is perfectly well
  // known, because the headline numbers fall back to `/stats` and these two have no such
  // fallback. A hint is a claim like any other; it does not get to say "0 enabled" because
  // nobody answered.
  services: { total: number; enabled?: number; ok: number; error: number; failed?: boolean };
  providers: { total: number; enabled: number; healthy: number; failed?: boolean };
  certificates: { expiring: number; total: number; thresholdDays: number; failed?: boolean };
  logs: { today: number; todayCapped: boolean; total?: number; failed?: boolean };
}

/** The six KPI tiles under the header. Every tile opens the page (and filter) it counts. */
export function StatRow({ loading, services, providers, certificates, logs }: StatRowProps) {
  const t = useT();
  const navigate = useNavigate();
  const { formatNumber, formatPercent } = useFormat();

  const okPercent = services.total > 0 ? Math.round((services.ok / services.total) * 100) : 0;

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <StatCard
        label={t('dashboard.stats.services')}
        value={services.failed ? '—' : formatNumber(services.total)}
        hint={
          services.enabled === undefined
            ? t('dashboard.stats.services_unknown')
            : t('dashboard.stats.services_hint', { count: services.enabled })
        }
        icon={<Globe />}
        tone={services.failed ? 'neutral' : 'primary'}
        loading={loading.services}
        onClick={() => navigate('/services')}
      />
      <StatCard
        label={t('dashboard.stats.services_ok')}
        value={services.failed ? '—' : formatNumber(services.ok)}
        hint={
          services.failed
            ? t('dashboard.stats.services_unknown')
            : t('dashboard.stats.services_ok_hint', { percent: formatPercent(okPercent) })
        }
        icon={<Activity />}
        tone={services.failed ? 'neutral' : 'success'}
        loading={loading.services}
        onClick={() => navigate('/monitoring?status=ok')}
      />
      <StatCard
        label={t('dashboard.stats.services_error')}
        value={services.failed ? '—' : formatNumber(services.error)}
        hint={
          services.failed
            ? t('dashboard.stats.services_unknown')
            : services.error > 0
              ? t('dashboard.stats.services_error_hint')
              : t('dashboard.stats.services_error_none')
        }
        icon={<AlertTriangle />}
        tone={!services.failed && services.error > 0 ? 'danger' : 'neutral'}
        loading={loading.services}
        onClick={() => navigate('/services?status=error')}
      />
      <StatCard
        label={t('dashboard.stats.providers')}
        value={providers.failed ? '—' : formatNumber(providers.total)}
        hint={
          providers.failed
            ? t('dashboard.stats.providers_unknown')
            : t('dashboard.stats.providers_hint', {
                healthy: t('dashboard.stats.providers_healthy', { count: providers.healthy }),
                enabled: t('dashboard.stats.providers_enabled', { count: providers.enabled }),
              })
        }
        icon={<Plug />}
        tone={providers.failed ? 'neutral' : 'info'}
        loading={loading.providers}
        onClick={() => navigate('/providers')}
      />
      <StatCard
        label={t('dashboard.stats.certificates')}
        value={certificates.failed ? '—' : formatNumber(certificates.expiring)}
        hint={
          certificates.failed
            ? t('dashboard.stats.certificates_unknown')
            : t('dashboard.stats.certificates_hint', {
                count: certificates.total,
                days: formatNumber(certificates.thresholdDays),
              })
        }
        icon={<ShieldCheck />}
        tone={certificates.failed || certificates.expiring > 0 ? 'warning' : 'neutral'}
        loading={loading.certificates}
        onClick={() => navigate('/certificates')}
      />
      <StatCard
        label={t('dashboard.stats.logs_today')}
        value={logs.failed ? '—' : logs.todayCapped ? `${formatNumber(logs.today)}+` : formatNumber(logs.today)}
        hint={
          logs.total === undefined
            ? t('dashboard.stats.logs_unknown')
            : t('dashboard.stats.logs_today_hint', { count: logs.total })
        }
        icon={<ScrollText />}
        tone="neutral"
        loading={loading.logs}
        onClick={() => navigate('/settings?tab=logs')}
      />
    </div>
  );
}
