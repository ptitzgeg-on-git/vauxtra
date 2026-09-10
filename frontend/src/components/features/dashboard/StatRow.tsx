import { useNavigate } from 'react-router-dom';
import { Activity, AlertTriangle, Globe, Plug, ScrollText, ShieldCheck } from 'lucide-react';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { StatCard } from '@/components/ui';

export interface StatRowProps {
  loading: {
    services: boolean;
    providers: boolean;
    certificates: boolean;
    logs: boolean;
  };
  services: { total: number; enabled: number; ok: number; error: number };
  providers: { total: number; enabled: number; healthy: number };
  // `failed` is what stops this tile reporting a confident zero when the expiry check did
  // not come back. Zero expiring and "we could not ask" look identical as a number, and
  // only one of them means everything is fine.
  certificates: { expiring: number; total: number; thresholdDays: number; failed?: boolean };
  logs: { today: number; todayCapped: boolean; total: number };
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
        value={formatNumber(services.total)}
        hint={t('dashboard.stats.services_hint', { enabled: formatNumber(services.enabled) })}
        icon={<Globe />}
        tone="primary"
        loading={loading.services}
        onClick={() => navigate('/services')}
      />
      <StatCard
        label={t('dashboard.stats.services_ok')}
        value={formatNumber(services.ok)}
        hint={t('dashboard.stats.services_ok_hint', { percent: formatPercent(okPercent) })}
        icon={<Activity />}
        tone="success"
        loading={loading.services}
        onClick={() => navigate('/monitoring?status=ok')}
      />
      <StatCard
        label={t('dashboard.stats.services_error')}
        value={formatNumber(services.error)}
        hint={services.error > 0 ? t('dashboard.stats.services_error_hint') : t('dashboard.stats.services_error_none')}
        icon={<AlertTriangle />}
        tone={services.error > 0 ? 'danger' : 'neutral'}
        loading={loading.services}
        onClick={() => navigate('/services?status=error')}
      />
      <StatCard
        label={t('dashboard.stats.providers')}
        value={formatNumber(providers.total)}
        hint={t('dashboard.stats.providers_hint', {
          healthy: formatNumber(providers.healthy),
          enabled: formatNumber(providers.enabled),
        })}
        icon={<Plug />}
        tone="info"
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
                total: formatNumber(certificates.total),
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
        value={logs.todayCapped ? `${formatNumber(logs.today)}+` : formatNumber(logs.today)}
        hint={t('dashboard.stats.logs_today_hint', { total: formatNumber(logs.total) })}
        icon={<ScrollText />}
        tone="neutral"
        loading={loading.logs}
        onClick={() => navigate('/settings?tab=logs')}
      />
    </div>
  );
}
