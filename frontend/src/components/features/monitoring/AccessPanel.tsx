import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { CloudOff, KeyRound, LockOpen, RefreshCw, ShieldCheck, ShieldPlus } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { getHttpStatus, translateApiError } from '@/lib/errors';
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Checkbox,
  EmptyState,
  InlineAlert,
  Modal,
  ProviderLogo,
  SkeletonCard,
  buttonVariants,
  cn,
  type Tone,
} from '@/components/ui';
import type {
  AccessHost,
  AccessHostState,
  AccessPermission,
  AccessProtectBody,
  AccessProtectResult,
  AccessPolicySummary,
  TunnelAccessItem,
  TunnelAccessResponse,
} from '@/types/api';
import { TUNNEL_ACCESS_KEY } from './useTunnelAccess';

/**
 * Which tunnel routes ask for a login, from `GET /api/providers/tunnels/access`.
 *
 * A tunnel publishes a service to the whole internet; Cloudflare Access is what puts a login in
 * front of it, and it lives in another part of the Cloudflare dashboard. A route can therefore
 * be published and open without anyone noticing. This tab reads both and says, route by route,
 * whether a login is asked, and why not when it is not.
 *
 * Two things it never does: show a policy's rules (who is allowed is personal data, only names
 * and decisions come back), and guess. When the token cannot read Access the routes are
 * `unknown`, the banner says which permission is missing, and nothing is called open.
 *
 * Protecting a route creates an Access application with policies that already exist in the
 * account (`POST /api/providers/{pid}/access/apps`). Writing policies stays in Cloudflare.
 */

const STATE_TONE: Record<AccessHostState, Tone> = {
  protected: 'success',
  partial: 'warning',
  open: 'danger',
  unknown: 'neutral',
};

const STATE_KEY: Record<AccessHostState, string> = {
  protected: 'monitoring.access.state.protected',
  partial: 'monitoring.access.state.partial',
  open: 'monitoring.access.state.open',
  unknown: 'monitoring.access.state.unknown',
};

/** Open first: the list is read for what needs doing. */
const STATE_RANK: Record<AccessHostState, number> = { open: 0, partial: 1, unknown: 2, protected: 3 };

const PERMISSION_KEY: Record<Exclude<AccessPermission, 'ok'>, string> = {
  unverified: 'monitoring.access.permission.unverified',
  missing: 'monitoring.access.permission.missing',
  error: 'monitoring.access.permission.error',
};

const REASON_KEY: Record<string, string> = {
  empty_listing: 'monitoring.access.reason.empty_listing',
  access_observed: 'monitoring.access.reason.access_observed',
  refused: 'monitoring.access.reason.refused',
  unreachable: 'monitoring.access.reason.unreachable',
  missing_account_id: 'monitoring.access.reason.missing_account_id',
  no_application: 'monitoring.access.reason.no_application',
  path_only: 'monitoring.access.reason.path_only',
  everyone_bypass: 'monitoring.access.reason.everyone_bypass',
  everyone_allow: 'monitoring.access.reason.everyone_allow',
  seen_at_edge: 'monitoring.access.reason.seen_at_edge',
};

/** The reasons a token permission fixes; the others are fixed elsewhere or by waiting. */
const TOKEN_REASONS = new Set(['empty_listing', 'access_observed', 'refused']);

const DECISION_KEY: Record<string, string> = {
  allow: 'monitoring.access.decision.allow',
  bypass: 'monitoring.access.decision.bypass',
  deny: 'monitoring.access.decision.deny',
  non_identity: 'monitoring.access.decision.non_identity',
};

function sortHosts(hosts: AccessHost[]): AccessHost[] {
  return [...hosts].sort(
    (a, b) => STATE_RANK[a.state] - STATE_RANK[b.state] || a.hostname.localeCompare(b.hostname),
  );
}

function canProtect(item: TunnelAccessItem, host: AccessHost): boolean {
  return item.permission === 'ok' && (host.state === 'open' || host.state === 'partial');
}

export interface AccessPanelProps {
  data: TunnelAccessResponse | undefined;
  loading: boolean;
  isError: boolean;
  refreshing: boolean;
  onRefresh: () => void;
}

export function AccessPanel({ data, loading, isError, refreshing, onRefresh }: AccessPanelProps) {
  const t = useT();
  const [target, setTarget] = useState<{ item: TunnelAccessItem; host: string } | null>(null);
  const items = Array.isArray(data?.items) ? data.items : [];

  return (
    <section className="space-y-4" aria-labelledby="monitoring-access-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 max-w-2xl">
          <h2 id="monitoring-access-title" className="flex items-center gap-2 text-base font-semibold text-foreground">
            <ShieldCheck className="h-4 w-4 text-muted-foreground" aria-hidden />
            {t('monitoring.access.title')}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('monitoring.access.description')}</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          leftIcon={<RefreshCw className={cn(refreshing && 'animate-spin')} />}
          disabled={refreshing}
          onClick={onRefresh}
        >
          {t('monitoring.access.refresh')}
        </Button>
      </div>

      {loading ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : isError ? (
        <InlineAlert
          tone="danger"
          title={t('monitoring.access.load_failed')}
          action={
            <Button variant="outline" size="sm" onClick={onRefresh}>
              {t('common.retry')}
            </Button>
          }
        >
          {t('monitoring.access.load_failed_hint')}
        </InlineAlert>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            icon={<CloudOff />}
            title={t('monitoring.access.empty')}
            description={t('monitoring.access.empty_hint')}
            action={
              <Link to="/providers" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
                {t('monitoring.tunnels.open_providers')}
              </Link>
            }
          />
        </Card>
      ) : (
        items.map((item) => (
          <IntegrationAccess key={item.id} item={item} onProtect={(host) => setTarget({ item, host })} />
        ))
      )}

      <ProtectModal
        key={target ? `${target.item.id}:${target.host}` : 'closed'}
        target={target}
        onClose={() => setTarget(null)}
      />
    </section>
  );
}

function IntegrationAccess({ item, onProtect }: { item: TunnelAccessItem; onProtect: (host: string) => void }) {
  const t = useT();
  const hosts = sortHosts(Array.isArray(item.hosts) ? item.hosts : []);
  const readable = item.permission === 'ok';
  const open = item.counts?.open ?? 0;

  return (
    <Card className="min-w-0">
      <CardHeader className="gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="flex min-w-0 items-center gap-2">
            <ProviderLogo type="cloudflare_tunnel" className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="truncate">{item.name}</span>
          </CardTitle>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge size="sm">{t('monitoring.access.routes', { count: hosts.length })}</Badge>
            {readable && (
              <Badge size="sm" tone="info">
                {t('monitoring.access.applications', { count: item.application_count })}
              </Badge>
            )}
          </div>
        </div>

        {item.permission !== 'ok' && (
          <InlineAlert
            tone={item.permission === 'error' ? 'danger' : 'warning'}
            icon={<KeyRound />}
            title={t(PERMISSION_KEY[item.permission])}
          >
            {REASON_KEY[item.reason] && <p>{t(REASON_KEY[item.reason])}</p>}
            {TOKEN_REASONS.has(item.reason) && (
              <div className="mt-2">
                <p className="font-medium">{t('monitoring.access.steps.title')}</p>
                <ol className="mt-1 list-decimal space-y-0.5 pl-5">
                  <li>{t('monitoring.access.steps.open')}</li>
                  <li>
                    {t('monitoring.access.steps.read', { name: item.read_permission })}
                  </li>
                  <li>
                    {t('monitoring.access.steps.edit', { name: item.edit_permission })}
                  </li>
                  <li>{t('monitoring.access.steps.save')}</li>
                </ol>
              </div>
            )}
            {item.reason === 'missing_account_id' && (
              <Link to="/providers" className={cn(buttonVariants({ variant: 'outline', size: 'sm' }), 'mt-2')}>
                {t('monitoring.tunnels.open_providers')}
              </Link>
            )}
          </InlineAlert>
        )}

        {readable && open > 0 && (
          <InlineAlert tone="danger" icon={<LockOpen />} title={t('monitoring.access.open_warning', { count: open })}>
            {t('monitoring.access.open_warning_hint')}
          </InlineAlert>
        )}
      </CardHeader>

      <CardContent>
        <ul className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
          {hosts.map((host) => (
            <HostTile
              key={host.hostname}
              host={host}
              readable={readable}
              protectable={canProtect(item, host)}
              onProtect={() => onProtect(host.hostname)}
            />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function HostTile({
  host,
  readable,
  protectable,
  onProtect,
}: {
  host: AccessHost;
  readable: boolean;
  protectable: boolean;
  onProtect: () => void;
}) {
  const t = useT();
  // Without the permission every host carries the integration's reason, which the banner
  // above already states once; repeating it on each tile would only add noise.
  const reasonKey = readable || host.reason === 'seen_at_edge' ? REASON_KEY[host.reason] : undefined;
  const observed = (host.observed || '').startsWith('edge_access');

  return (
    <li
      className={cn(
        'flex min-w-0 flex-col gap-2 rounded-xl border bg-card p-3',
        host.state === 'open' ? 'border-destructive/40' : 'border-border',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 break-all text-sm font-medium text-foreground">{host.hostname}</p>
        <Badge tone={STATE_TONE[host.state]} size="sm" dot className="shrink-0">
          {t(STATE_KEY[host.state])}
        </Badge>
      </div>

      {reasonKey && <p className="text-xs text-muted-foreground">{t(reasonKey)}</p>}

      {host.policies.length > 0 && (
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{t('monitoring.access.col.policies')}</span>
          {': '}
          {host.policies.join(', ')}
        </p>
      )}

      {(!host.managed || observed) && (
        <div className="flex flex-wrap gap-1.5">
          {!host.managed && (
            <Badge size="sm" title={t('monitoring.access.unmanaged_hint')}>
              {t('monitoring.access.unmanaged')}
              <span className="sr-only">: {t('monitoring.access.unmanaged_hint')}</span>
            </Badge>
          )}
          {observed && (
            <Badge size="sm" tone="info" title={t('monitoring.access.observed_hint')}>
              {t('monitoring.access.observed')}
              <span className="sr-only">: {t('monitoring.access.observed_hint')}</span>
            </Badge>
          )}
        </div>
      )}

      {protectable && (
        <div className="mt-auto pt-1">
          <Button
            variant="outline"
            size="sm"
            leftIcon={<ShieldPlus />}
            aria-label={t('monitoring.access.protect_host', { host: host.hostname })}
            onClick={onProtect}
          >
            {t('monitoring.access.protect')}
          </Button>
        </div>
      )}
    </li>
  );
}

export function PolicyOption({
  policy,
  checked,
  onChange,
}: {
  policy: AccessPolicySummary;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  const t = useT();
  const decisionKey = DECISION_KEY[policy.decision];
  return (
    <li className="rounded-lg border border-border p-2.5">
      <Checkbox
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        label={
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-foreground">{policy.name}</span>
            <Badge size="sm" tone={policy.decision === 'allow' ? 'info' : 'neutral'}>
              {decisionKey ? t(decisionKey) : policy.decision}
            </Badge>
          </span>
        }
        description={
          policy.everyone ? (
            <span className="text-warning">{t('monitoring.access.modal.everyone')}</span>
          ) : (
            t('monitoring.access.modal.used_by', { count: policy.app_count })
          )
        }
      />
    </li>
  );
}

function ProtectModal({
  target,
  onClose,
}: {
  target: { item: TunnelAccessItem; host: string } | null;
  onClose: () => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string[]>([]);

  const protect = useMutation({
    mutationFn: ({ id, body }: { id: number; body: AccessProtectBody }) =>
      api.post<AccessProtectResult>(`/providers/${id}/access/apps`, body),
    onSuccess: async (result, { body }) => {
      toast.success(t('monitoring.access.toast.protected', { host: result?.hostname || body.hostname }));
      onClose();
      // The services too: the route now carries the application's id, and the delete dialog
      // of the routes page reads it there to say the login goes with the service.
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: TUNNEL_ACCESS_KEY }),
        queryClient.invalidateQueries({ queryKey: ['services'] }),
      ]);
    },
    onError: (err: unknown) => {
      if (getHttpStatus(err) === 403 && target) {
        toast.error(t('monitoring.access.edit_needed', { name: target.item.edit_permission }));
        return;
      }
      // A 409 means someone protected it meanwhile: the list is stale, not the request.
      if (getHttpStatus(err) === 409) void queryClient.invalidateQueries({ queryKey: TUNNEL_ACCESS_KEY });
      toast.error(translateApiError(err, t, t('monitoring.access.toast.failed')));
    },
  });

  if (!target) return null;
  const policies = Array.isArray(target.item.policies) ? target.item.policies : [];

  const toggle = (id: string, on: boolean) =>
    setSelected((current) => (on ? [...current, id] : current.filter((value) => value !== id)));

  return (
    <Modal
      open
      onClose={onClose}
      size="md"
      icon={<ShieldPlus />}
      title={t('monitoring.access.protect_host', { host: target.host })}
      description={t('monitoring.access.modal.description', { host: target.host })}
      footer={
        <div className="flex w-full flex-wrap items-center justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            leftIcon={<ShieldPlus />}
            loading={protect.isPending}
            disabled={selected.length === 0}
            onClick={() =>
              protect.mutate({ id: target.item.id, body: { hostname: target.host, policy_ids: selected } })
            }
          >
            {t('monitoring.access.modal.submit')}
          </Button>
        </div>
      }
    >
      {policies.length === 0 ? (
        <InlineAlert tone="info">{t('monitoring.access.modal.no_policies')}</InlineAlert>
      ) : (
        <fieldset className="space-y-2">
          <legend className="mb-2 text-sm font-medium text-foreground">{t('monitoring.access.modal.policies')}</legend>
          <ul className="space-y-2">
            {policies.map((policy) => (
              <PolicyOption
                key={policy.id}
                policy={policy}
                checked={selected.includes(policy.id)}
                onChange={(on) => toggle(policy.id, on)}
              />
            ))}
          </ul>
          {selected.length === 0 && (
            <p className="text-xs text-muted-foreground">{t('monitoring.access.modal.pick_hint')}</p>
          )}
        </fieldset>
      )}
    </Modal>
  );
}
