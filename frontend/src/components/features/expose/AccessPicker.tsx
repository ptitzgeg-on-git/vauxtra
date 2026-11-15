import { useId } from 'react';
import { Link } from 'react-router-dom';
import { ShieldCheck } from 'lucide-react';
import { useT } from '@/i18n';
import { InlineAlert, Skeleton } from '@/components/ui';
import { PolicyOption } from '@/components/features/monitoring/AccessPanel';
import { useTunnelAccess } from '@/components/features/monitoring/useTunnelAccess';

/**
 * The optional Cloudflare Access step of a tunnel route, on the service form.
 *
 * A tunnel publishes the service to the whole internet, and the login in front of it lives in
 * another part of the Cloudflare dashboard. The Access tab of the Monitoring page finds the
 * routes left open after the fact; this lets the operator close one while creating it.
 *
 * Nothing is written from here. The form keeps the chosen policy ids, and the save creates the
 * Access application once the tunnel serves the hostname (`POST /providers/{id}/access/apps`
 * refuses a hostname the tunnel does not route). Writing policies stays in Cloudflare: only
 * reusable policies of the tunnel's account are offered.
 *
 * Unchecked, the route is published exactly as before. A token that cannot read Access, or a
 * read that failed, says so and offers nothing rather than an empty list that reads as "this
 * account has no policies".
 */
export function AccessPicker({
  providerId,
  hostname,
  selected,
  onChange,
}: {
  providerId: string;
  hostname: string;
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const t = useT();
  const titleId = useId();
  const { data, isLoading, isError } = useTunnelAccess();

  const items = Array.isArray(data?.items) ? data.items : [];
  const item = items.find((entry) => String(entry.id) === providerId);
  const host = hostname.trim().toLowerCase();
  const existing = item?.hosts?.find((entry) => entry.hostname === host);

  let body;
  if (isLoading) {
    body = <Skeleton className="h-16 w-full" />;
  } else if (isError || !item || item.permission === 'error') {
    body = <p className="text-xs text-muted-foreground">{t('expose.access.unread')}</p>;
  } else if (item.permission !== 'ok') {
    body = (
      <p className="text-xs text-muted-foreground">
        {t('expose.access.cannot_read', { name: item.read_permission })}
      </p>
    );
  } else if (existing && (existing.state === 'protected' || existing.state === 'partial')) {
    // Creating a second application for a hostname Access already covers is refused (409)
    // for `protected`, and for `partial` it would stack a policy on a setup someone wrote by
    // hand. Either way the place to change it is the Access tab.
    body = (
      <p className="flex items-start gap-2 text-xs text-muted-foreground">
        <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" aria-hidden="true" />
        <span>
          {t('expose.access.already', { host, name: existing.apps.map((app) => app.name).join(', ') })}{' '}
          <Link to="/monitoring?tab=access" className="font-medium text-primary hover:underline">
            {t('expose.access.open_tab')}
          </Link>
        </span>
      </p>
    );
  } else if (!Array.isArray(item.policies) || item.policies.length === 0) {
    body = <InlineAlert tone="info">{t('monitoring.access.modal.no_policies')}</InlineAlert>;
  } else {
    const policies = item.policies;
    const toggle = (id: string, on: boolean) =>
      onChange(on ? [...selected.filter((value) => value !== id), id] : selected.filter((value) => value !== id));
    body = (
      <>
        <ul className="max-h-56 space-y-2 overflow-auto">
          {policies.map((policy) => (
            <PolicyOption
              key={policy.id}
              policy={policy}
              checked={selected.includes(policy.id)}
              onChange={(on) => toggle(policy.id, on)}
            />
          ))}
        </ul>
        <p className="text-xs text-muted-foreground">
          {selected.length === 0
            ? t('expose.access.none_checked')
            : t('expose.access.edit_needed', { name: item.edit_permission })}
        </p>
      </>
    );
  }

  return (
    <div role="group" aria-labelledby={titleId} className="space-y-2 rounded-xl border border-border bg-card p-3">
      <div>
        <p id={titleId} className="text-xs font-semibold leading-tight text-foreground">
          {t('expose.access.title')}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('expose.access.description')}</p>
      </div>
      {body}
    </div>
  );
}
