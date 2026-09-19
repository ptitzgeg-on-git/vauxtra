import { type Dispatch, type ReactNode, type SetStateAction, useId } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRightLeft, Globe, RefreshCw, Server, Waypoints } from 'lucide-react';
import toast from 'react-hot-toast';
import { useT } from '@/i18n';
import { cn } from '@/lib/cn';
import {
  domainProblem,
  domainProblemKey,
  fqdnProblem,
  fqdnProblemKey,
  subdomainProblem,
  subdomainProblemKey,
} from '@/lib/hostname';
import {
  Button,
  Checkbox,
  Chip,
  ChipGroup,
  Field,
  FieldHint,
  InlineAlert,
  Input,
  SectionHeading,
  Select,
  Skeleton,
  Switch,
} from '@/components/ui';
import type { Environment, ProviderTypesResponse, Tag } from '@/types/api';
import {
  autoPublicTarget,
  fqdnOf,
  providerHasCapability,
  type FormState,
  type Provider,
  type UiExposeMode,
} from './types';

interface TargetSuggestion {
  candidates: Array<{ value: string; source: string }>;
  recommended: string;
}

interface ServiceFormProps {
  formData: FormState;
  setFormData: Dispatch<SetStateAction<FormState>>;
  providers: Provider[];
  domains: string[];
  /**
   * A list below came back empty because its request failed, not because it is empty. One
   * flag each: they are four separate requests, and the one that answered must not be made
   * to apologise for the one that did not.
   */
  providersError?: boolean;
  domainsError?: boolean;
  tagsError?: boolean;
  environmentsError?: boolean;
  isLoadingProviders: boolean;
  /** Narrowed to the failed read: the busy state also disables the button it sits on. */
  isRefetchingProviders?: boolean;
  refetchProviders?: () => void;
  isLoadingDomains: boolean;
  providerTypeMap: ProviderTypesResponse;
  targetSuggestion: TargetSuggestion | undefined;
  isFetchingTargetSuggestion: boolean;
  /**
   * The public-target lookup failed, rather than answering that there is no target. The
   * suggestion itself cannot tell the two apart: both arrive as an absent `recommended`.
   */
  targetSuggestionError?: boolean;
  refetchTargetSuggestion: () => void;
  tags: Tag[];
  environments: Environment[];
  isLoadingTaxonomy: boolean;
}

const MODE_ICONS: Record<UiExposeMode, ReactNode> = {
  dns_only: <Globe />,
  dns_proxy: <ArrowRightLeft />,
  tunnel: <Waypoints />,
};

/** One of the three exposure modes, as a selectable card wrapping a real radio input. */
function ModeOption({
  name,
  value,
  current,
  disabled,
  title,
  description,
  onSelect,
  children,
}: {
  name: string;
  value: UiExposeMode;
  current: UiExposeMode;
  disabled?: boolean;
  title: string;
  description: string;
  onSelect: () => void;
  children?: ReactNode;
}) {
  const checked = current === value;
  return (
    <label
      className={cn(
        'relative flex cursor-pointer gap-3 rounded-xl border p-3 transition-colors duration-150',
        checked ? 'border-primary bg-primary/5 shadow-sm' : 'border-border bg-card hover:bg-accent/60',
        disabled && 'cursor-not-allowed opacity-60 hover:bg-card',
        'has-focus-visible:ring-2 has-focus-visible:ring-ring has-focus-visible:ring-offset-2 has-focus-visible:ring-offset-background',
      )}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        disabled={disabled}
        onChange={onSelect}
        className="sr-only"
      />
      <span
        aria-hidden="true"
        className={cn(
          'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg [&>svg]:h-4 [&>svg]:w-4',
          checked ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground',
        )}
      >
        {MODE_ICONS[value]}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold text-foreground">{title}</span>
        <span className="mt-0.5 block text-xs text-muted-foreground">{description}</span>
        {children}
      </span>
    </label>
  );
}

/** A scrollable list of "push to this provider as well" checkboxes. */
function ExtraProviderList({
  label,
  providers,
  selectedIds,
  onToggle,
  emptyText,
}: {
  label: string;
  providers: Provider[];
  selectedIds: string[];
  onToggle: (id: string, checked: boolean) => void;
  emptyText: string;
}) {
  const id = useId();
  return (
    <div className="space-y-2">
      <p id={id} className="text-xs font-semibold leading-tight text-foreground">
        {label}
      </p>
      <div role="group" aria-labelledby={id} className="max-h-36 space-y-2 overflow-auto rounded-xl border border-border bg-card p-3">
        {providers.map((p) => (
          <Checkbox
            key={p.id}
            checked={selectedIds.includes(String(p.id))}
            onChange={(e) => onToggle(String(p.id), e.target.checked)}
            label={`${p.name} · ${p.type}`}
          />
        ))}
        {providers.length === 0 && <p className="text-xs text-muted-foreground">{emptyText}</p>}
      </div>
    </div>
  );
}

/** Tags or environments as toggle chips; `color` comes from the record and paints the dot. */
function TaxonomyChips({
  label,
  items,
  selected,
  onToggle,
  emptyText,
  loading,
}: {
  label: string;
  items: Array<Tag | Environment>;
  selected: number[];
  onToggle: (id: number) => void;
  emptyText: ReactNode;
  loading: boolean;
}) {
  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold leading-tight text-foreground">{label}</p>
      {loading ? (
        <div className="flex gap-2">
          <Skeleton className="h-7 w-20 rounded-full" />
          <Skeleton className="h-7 w-16 rounded-full" />
          <Skeleton className="h-7 w-24 rounded-full" />
        </div>
      ) : items.length === 0 ? (
        <p className="text-xs text-muted-foreground">{emptyText}</p>
      ) : (
        <ChipGroup label={label}>
          {items.map((item) => (
            <Chip
              key={item.id}
              size="sm"
              selected={selected.includes(item.id)}
              onClick={() => onToggle(item.id)}
              icon={
                item.color ? (
                  <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: item.color }} />
                ) : undefined
              }
            >
              {item.name}
            </Chip>
          ))}
        </ChipGroup>
      )}
    </div>
  );
}

export function ServiceForm({
  formData,
  setFormData,
  providers,
  domains,
  providersError = false,
  domainsError = false,
  tagsError = false,
  environmentsError = false,
  isLoadingProviders,
  isRefetchingProviders = false,
  refetchProviders,
  isLoadingDomains,
  providerTypeMap,
  targetSuggestion,
  isFetchingTargetSuggestion,
  targetSuggestionError,
  refetchTargetSuggestion,
  tags,
  environments,
  isLoadingTaxonomy,
}: ServiceFormProps) {
  const t = useT();
  const modeGroupName = useId();
  const domainListId = useId();

  // A list below is empty because the read failed, not because there is nothing in it. A
  // refresh that fails over rows that already arrived leaves those rows in the selects, and
  // a list that is empty at that point is empty for a reason worth stating as its own.
  const providersUnread = providersError && providers.length === 0;

  // The public-target lookup has three outcomes and the field showed only one of them. A
  // suggestion that arrived proves the read succeeded, so nothing beyond `targetSuggestion`
  // is needed to tell "answered with nothing" from "never answered"; the failure, though,
  // is only knowable from the query, hence the prop.
  const targetLookupFailed = Boolean(targetSuggestionError);
  const targetLookupFoundNothing =
    !targetSuggestionError &&
    targetSuggestion !== undefined &&
    !String(targetSuggestion.recommended || '').trim();

  const allProviders = providers.filter((p) => Boolean(p.enabled));
  const proxyProviders = allProviders.filter((p) => providerHasCapability(p, 'proxy', providerTypeMap));
  const dnsProviders = allProviders.filter((p) => providerHasCapability(p, 'dns', providerTypeMap));
  const tunnelProviders = proxyProviders.filter((p) => providerHasCapability(p, 'supports_tunnel', providerTypeMap));
  const hasTunnelProvider = tunnelProviders.length > 0;
  // Standard (non-tunnel) proxy providers — used for "Primary reverse proxy" in proxy_dns
  // mode and for "additional reverse providers" in both modes.
  const tunnelIds = new Set(tunnelProviders.map((p) => p.id));
  const standardProxyProviders = proxyProviders.filter((p) => !tunnelIds.has(p.id));

  const selectedDns = dnsProviders.find((p) => String(p.id) === formData.dns_provider_id);
  // Same call as the payload builder in `ExposeModal`: this is the whole point of it
  // living in `types.ts`. Drawing one answer and sending another is what it prevents.
  const publicTarget = autoPublicTarget(formData, selectedDns, providerTypeMap);

  // Use provider capabilities instead of hardcoded types for long-term extensibility.
  const isExternalDns = selectedDns ? providerHasCapability(selectedDns, 'public_dns', providerTypeMap) : false;
  const isLocalDns = Boolean(selectedDns) && !isExternalDns;

  const fqdnPreview = fqdnOf(formData) ?? t('expose.preview.host_placeholder');

  // The same rules the server applies, so the field says which one is broken instead of
  // letting "Continue" spend a round trip on a name that cannot be accepted. Only once
  // something has been typed: an empty required field is already marked as such, and a form
  // that opens shouting at every blank is a form nobody reads.
  const subdomainError = formData.subdomain ? subdomainProblem(formData.subdomain, { allowWildcard: true }) : null;
  const domainError = formData.domain ? domainProblem(formData.domain) : null;
  // The rule about the name the two halves make, which neither field can ask on its own.
  // It shows under the subdomain: that is where the composite preview lives, and the half
  // an operator would shorten. Only once both halves are otherwise sound, so a name that is
  // both malformed and too long says the first thing to fix rather than the second.
  const fqdnError =
    !subdomainError && !domainError && formData.subdomain && formData.domain
      ? fqdnProblem(formData.subdomain, formData.domain)
      : null;
  // `subdomain.domain` is a claim about both halves, so one broken half makes the whole
  // preview a promise the server will not keep. It comes back when the name is publishable.
  const showFqdnPreview = !subdomainError && !domainError && !fqdnError;

  // Auto-sync tunnel_hostname when subdomain/domain change in tunnel mode.
  // Only auto-fill when the user hasn't typed a custom hostname.
  const prevFqdn = `${formData.subdomain}.${formData.domain}`;
  const tunnelHostnameIsDefault = !formData.tunnel_hostname || formData.tunnel_hostname === prevFqdn;

  const parseTargetInput = (value: string) => {
    const trimmed = value.trim();
    if (!trimmed || !/^https?:\/\//i.test(trimmed)) {
      setFormData((prev) => ({ ...prev, target_ip: value }));
      return;
    }
    try {
      const parsed = new URL(trimmed);
      const parsedPort = parsed.port ? Number(parsed.port) : parsed.protocol === 'https:' ? 443 : 80;
      setFormData((prev) => ({
        ...prev,
        target_ip: parsed.hostname,
        target_port: Number.isFinite(parsedPort) ? parsedPort : prev.target_port,
        forward_scheme: parsed.protocol === 'https:' ? 'https' : 'http',
      }));
      toast.success(t('expose.target_parsed'));
    } catch {
      setFormData((prev) => ({ ...prev, target_ip: value }));
    }
  };

  const toggleExtra = (role: 'proxy' | 'dns', providerId: string, checked: boolean) => {
    if (role === 'proxy') {
      setFormData((prev) => ({
        ...prev,
        extra_proxy_provider_ids: checked
          ? [...prev.extra_proxy_provider_ids, providerId]
          : prev.extra_proxy_provider_ids.filter((id) => id !== providerId),
      }));
    } else {
      setFormData((prev) => ({
        ...prev,
        extra_dns_provider_ids: checked
          ? [...prev.extra_dns_provider_ids, providerId]
          : prev.extra_dns_provider_ids.filter((id) => id !== providerId),
      }));
    }
  };

  const toggleTag = (id: number) =>
    setFormData((prev) => ({
      ...prev,
      tag_ids: prev.tag_ids.includes(id) ? prev.tag_ids.filter((x) => x !== id) : [...prev.tag_ids, id],
    }));

  const toggleEnvironment = (id: number) =>
    setFormData((prev) => ({
      ...prev,
      environment_ids: prev.environment_ids.includes(id)
        ? prev.environment_ids.filter((x) => x !== id)
        : [...prev.environment_ids, id],
    }));

  const providerOption = (p: Provider) => (
    <option key={p.id} value={p.id}>
      {p.name} · {p.type}
    </option>
  );

  const integrationsLink = (
    <Link to="/providers" className="font-medium text-primary hover:underline">
      {t('expose.mode.add_provider')}
    </Link>
  );

  const extraProxyCandidates = standardProxyProviders.filter((p) => String(p.id) !== formData.proxy_provider_id);
  const extraDnsCandidates = dnsProviders.filter((p) => String(p.id) !== formData.dns_provider_id);

  const dnsValidationError =
    formData.ui_expose_mode === 'dns_only' && !formData.dns_provider_id
      ? t('expose.validation.dns_required_dns_only')
      : formData.ui_expose_mode !== 'dns_only' && !formData.proxy_provider_id && !formData.dns_provider_id
        ? t('expose.validation.provider_required')
        : undefined;

  const dnsScopeHint = selectedDns
    ? isLocalDns
      ? formData.ui_expose_mode === 'dns_only'
        ? t('expose.dns_scope.local_dns_only')
        : t('expose.dns_scope.local')
      : t('expose.dns_scope.external')
    : undefined;

  return (
    <div className="space-y-8">
      {/* ── Section 1: Public route ── */}
      <section className="space-y-4">
        <SectionHeading
          as="h3"
          size="sm"
          title={t('expose.section.route.title')}
          description={t('expose.section.route.description')}
        />
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field
            label={t('expose.field.subdomain')}
            required
            error={
              subdomainError
                ? t(subdomainProblemKey(subdomainError))
                : fqdnError
                  ? t(fqdnProblemKey(fqdnError))
                  : undefined
            }
            hint={
              showFqdnPreview ? (
                <>
                  {t('expose.field.final_route')} <span className="font-mono text-foreground">{fqdnPreview}</span>
                </>
              ) : undefined
            }
          >
            <Input
              type="text"
              required
              autoComplete="off"
              spellCheck={false}
              className="font-mono"
              value={formData.subdomain}
              placeholder={t('expose.field.subdomain_placeholder')}
              onChange={(e) => {
                const sub = e.target.value.replace(/\s+/g, '').toLowerCase();
                setFormData((prev) => {
                  const next: FormState = { ...prev, subdomain: sub };
                  if (prev.expose_mode === 'tunnel' && tunnelHostnameIsDefault && sub && prev.domain) {
                    next.tunnel_hostname = `${sub}.${prev.domain}`;
                  }
                  return next;
                });
              }}
            />
          </Field>

          <Field
            label={t('expose.field.domain')}
            required
            error={domainError ? t(domainProblemKey(domainError)) : undefined}
            hint={
              domains.length === 0 && domainsError ? (
                // "You have no domains" and "we could not read your domains" are the same
                // empty array, and only one of them is worth a trip to the settings page.
                // Keyed on the list still being empty, because a refresh can fail over data
                // that already arrived -- and those domains are in the datalist below.
                t('expose.field.domain_hint_unavailable')
              ) : domains.length === 0 && !isLoadingDomains ? (
                <>
                  {t('expose.field.domain_hint_prefix')}{' '}
                  <Link to="/settings?tab=dns" className="font-medium text-primary hover:underline">
                    {t('expose.field.domain_hint_link')}
                  </Link>{' '}
                  {t('expose.field.domain_hint_suffix')}
                </>
              ) : undefined
            }
          >
            <Input
              list={domains.length > 0 ? domainListId : undefined}
              type="text"
              required
              autoComplete="off"
              spellCheck={false}
              placeholder={isLoadingDomains ? t('common.loading') : t('expose.field.domain_placeholder')}
              value={formData.domain}
              onChange={(e) => {
                const dom = e.target.value.trim().toLowerCase();
                setFormData((prev) => {
                  const next: FormState = { ...prev, domain: dom };
                  if (prev.expose_mode === 'tunnel' && tunnelHostnameIsDefault && prev.subdomain && dom) {
                    next.tunnel_hostname = `${prev.subdomain}.${dom}`;
                  }
                  return next;
                });
              }}
            />
          </Field>
          {domains.length > 0 && (
            <datalist id={domainListId}>
              {domains.map((domain) => (
                <option key={domain} value={domain} />
              ))}
            </datalist>
          )}
        </div>

        <fieldset className="space-y-2">
          <legend className="text-xs font-semibold leading-tight text-foreground">{t('expose.mode.label')}</legend>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <ModeOption
              name={modeGroupName}
              value="dns_only"
              current={formData.ui_expose_mode}
              disabled={dnsProviders.length === 0}
              title={t('expose.mode.dns_only')}
              description={t('expose.mode.dns_only_desc')}
              onSelect={() =>
                setFormData((prev) => ({
                  ...prev,
                  ui_expose_mode: 'dns_only',
                  expose_mode: 'proxy_dns',
                  proxy_provider_id: '',
                  extra_proxy_provider_ids: [],
                  tunnel_provider_id: '',
                  tunnel_hostname: '',
                }))
              }
            >
              {dnsProviders.length === 0 && (
                <span className="mt-1 block text-xs text-muted-foreground">
                  {t('expose.mode.no_dns_provider')} {integrationsLink}
                </span>
              )}
            </ModeOption>

            <ModeOption
              name={modeGroupName}
              value="dns_proxy"
              current={formData.ui_expose_mode}
              title={t('expose.mode.dns_proxy')}
              description={t('expose.mode.dns_proxy_desc')}
              onSelect={() =>
                setFormData((prev) => ({
                  ...prev,
                  ui_expose_mode: 'dns_proxy',
                  expose_mode: 'proxy_dns',
                  tunnel_provider_id: '',
                  tunnel_hostname: '',
                  extra_proxy_provider_ids: prev.expose_mode === 'tunnel' ? [] : prev.extra_proxy_provider_ids,
                }))
              }
            />

            <ModeOption
              name={modeGroupName}
              value="tunnel"
              current={formData.ui_expose_mode}
              disabled={!hasTunnelProvider}
              title={t('expose.mode.tunnel')}
              description={t('expose.mode.tunnel_desc')}
              onSelect={() =>
                setFormData((prev) => ({
                  ...prev,
                  ui_expose_mode: 'tunnel',
                  expose_mode: 'tunnel',
                  public_target_mode: 'manual',
                  auto_update_dns: false,
                  proxy_provider_id: '',
                  dns_provider_id: '',
                  dns_ip: '',
                  extra_proxy_provider_ids: [],
                  tunnel_hostname: prev.tunnel_hostname || fqdnOf(prev) || '',
                }))
              }
            >
              {!hasTunnelProvider && (
                <span className="mt-1 block text-xs text-muted-foreground">
                  {t('expose.mode.no_tunnel_provider')} {integrationsLink}
                </span>
              )}
            </ModeOption>
          </div>
        </fieldset>
      </section>

      {/* ── Section 2: Internal destination ── */}
      <section className="space-y-4">
        <SectionHeading
          as="h3"
          size="sm"
          title={t('expose.section.target.title')}
          description={t('expose.section.target.description')}
        />
        <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
          <div className="md:col-span-2">
            <Field label={t('expose.field.target')} required hint={t('expose.field.target_hint')}>
              <Input
                type="text"
                required
                autoComplete="off"
                spellCheck={false}
                className="font-mono"
                leftIcon={<Server />}
                value={formData.target_ip}
                onChange={(e) => parseTargetInput(e.target.value)}
                placeholder={t('expose.field.target_placeholder')}
              />
            </Field>
          </div>

          <Field label={t('expose.field.port')} required>
            <Input
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              required
              className="text-center font-mono font-semibold"
              value={formData.target_port === 0 ? '' : formData.target_port}
              onChange={(e) => {
                const raw = e.target.value.replace(/\D/g, '');
                const num = raw === '' ? 0 : Math.min(65535, Number(raw));
                setFormData((prev) => ({ ...prev, target_port: num }));
              }}
              onBlur={() => {
                if (!formData.target_port) setFormData((prev) => ({ ...prev, target_port: 80 }));
              }}
            />
          </Field>

          <Field label={t('expose.field.scheme')}>
            <Select
              value={formData.forward_scheme}
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, forward_scheme: e.target.value as 'http' | 'https' }))
              }
            >
              <option value="http">http</option>
              <option value="https">https</option>
            </Select>
          </Field>
        </div>

        <Switch
          checked={formData.websocket}
          onCheckedChange={(checked) => setFormData((prev) => ({ ...prev, websocket: checked }))}
          size="sm"
          label={t('expose.field.websocket')}
          description={t('expose.field.websocket_hint')}
        />
      </section>

      {/* ── Section 3: Provider strategy ── */}
      <section className="space-y-4">
        <SectionHeading
          as="h3"
          size="sm"
          title={t('expose.section.providers.title')}
          description={t('expose.section.providers.description')}
        />
        {providersUnread && (
          <InlineAlert
            tone="warning"
            title={t('expose.providers.unread')}
            action={
              refetchProviders && (
                <Button
                  variant="outline"
                  size="sm"
                  leftIcon={<RefreshCw />}
                  loading={isRefetchingProviders}
                  onClick={refetchProviders}
                >
                  {t('common.retry')}
                </Button>
              )
            }
          >
            {t('expose.providers.unread_hint')}
          </InlineAlert>
        )}
        {isLoadingProviders ? (
          <div className="space-y-3 rounded-xl border border-border bg-muted/40 p-4" aria-busy="true" aria-label={t('expose.providers.loading')}>
            <Skeleton className="h-4 w-40" />
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          </div>
        ) : (
          <div className="space-y-5">
            {formData.expose_mode === 'tunnel' && (
              <>
                <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
                  <Field label={t('expose.field.tunnel_provider')} required>
                    <Select
                      value={formData.tunnel_provider_id}
                      disabled={tunnelProviders.length === 0}
                      onChange={(e) => {
                        const nextId = e.target.value;
                        setFormData((prev) => ({
                          ...prev,
                          tunnel_provider_id: nextId,
                          extra_proxy_provider_ids: prev.extra_proxy_provider_ids.filter((id) => id !== nextId),
                        }));
                      }}
                    >
                      <option value="">{t('expose.field.tunnel_provider_placeholder')}</option>
                      {tunnelProviders.map(providerOption)}
                    </Select>
                  </Field>

                  <Field
                    label={t('expose.field.tunnel_hostname')}
                    hint={
                      tunnelHostnameIsDefault
                        ? t('expose.field.tunnel_hostname_auto')
                        : t('expose.field.tunnel_hostname_custom', { fqdn: fqdnPreview })
                    }
                  >
                    <Input
                      type="text"
                      autoComplete="off"
                      spellCheck={false}
                      className="font-mono"
                      value={formData.tunnel_hostname}
                      onChange={(e) =>
                        setFormData((prev) => ({ ...prev, tunnel_hostname: e.target.value.trim().toLowerCase() }))
                      }
                      placeholder={fqdnPreview}
                    />
                  </Field>
                </div>

                <ExtraProviderList
                  label={t('expose.field.extra_proxies')}
                  providers={standardProxyProviders}
                  selectedIds={formData.extra_proxy_provider_ids}
                  onToggle={(id, checked) => toggleExtra('proxy', id, checked)}
                  emptyText={providersUnread ? t('ui.error.list_unavailable') : t('expose.field.extra_proxies_empty')}
                />
              </>
            )}

            {formData.expose_mode === 'proxy_dns' && (
              <>
                <div className={cn('grid grid-cols-1 gap-5', formData.ui_expose_mode !== 'dns_only' && 'md:grid-cols-2')}>
                  {/* Primary reverse proxy — hidden in DNS-only mode */}
                  {formData.ui_expose_mode !== 'dns_only' && (
                    <Field label={t('expose.field.primary_proxy')}>
                      <Select
                        value={formData.proxy_provider_id}
                        disabled={standardProxyProviders.length === 0}
                        onChange={(e) => {
                          const nextProxyId = e.target.value;
                          const selectedProvider = standardProxyProviders.find((p) => String(p.id) === nextProxyId);
                          let inferredDnsTarget = '';
                          if (selectedProvider?.url) {
                            try {
                              inferredDnsTarget = new URL(selectedProvider.url).hostname;
                            } catch {
                              inferredDnsTarget = '';
                            }
                          }
                          setFormData((prev) => ({
                            ...prev,
                            proxy_provider_id: nextProxyId,
                            dns_ip: prev.dns_ip.trim() || !isLocalDns ? prev.dns_ip : inferredDnsTarget,
                            extra_proxy_provider_ids: prev.extra_proxy_provider_ids.filter((id) => id !== nextProxyId),
                          }));
                        }}
                      >
                        <option value="">{t('expose.field.none')}</option>
                        {standardProxyProviders.map(providerOption)}
                      </Select>
                    </Field>
                  )}

                  {/* Primary DNS provider — always shown */}
                  <Field
                    label={t('expose.field.primary_dns')}
                    required={formData.ui_expose_mode === 'dns_only'}
                    error={dnsValidationError}
                    hint={dnsScopeHint}
                  >
                    <Select
                      value={formData.dns_provider_id}
                      disabled={dnsProviders.length === 0}
                      invalid={Boolean(dnsValidationError)}
                      onChange={(e) => {
                        const nextDnsProviderId = e.target.value;
                        const nextDnsProvider = dnsProviders.find((p) => String(p.id) === nextDnsProviderId);

                        // Clear dns_ip when scope changes (local ↔ external) to avoid a stale
                        // LAN IP sitting in a field now labelled "Public WAN IP" and vice-versa.
                        const prevIsExternal = selectedDns
                          ? providerHasCapability(selectedDns, 'public_dns', providerTypeMap)
                          : false;
                        const nextIsExternal = nextDnsProvider
                          ? providerHasCapability(nextDnsProvider, 'public_dns', providerTypeMap)
                          : false;
                        const scopeChanged = nextDnsProviderId && prevIsExternal !== nextIsExternal;

                        setFormData((prev) => {
                          // The rule that decides what the next provider allows is the one
                          // the payload will apply anyway. Asking it here is what keeps the
                          // state the operator edits from meaning something else on save.
                          const next = autoPublicTarget(
                            { ...prev, dns_provider_id: nextDnsProviderId },
                            nextDnsProvider,
                            providerTypeMap,
                          );
                          return {
                            ...prev,
                            dns_provider_id: nextDnsProviderId,
                            dns_ip: scopeChanged ? '' : prev.dns_ip,
                            public_target_mode: next.mode,
                            auto_update_dns: next.autoUpdateDns,
                            extra_dns_provider_ids: prev.extra_dns_provider_ids.filter(
                              (id) => id !== nextDnsProviderId,
                            ),
                          };
                        });
                      }}
                    >
                      <option value="">{t('expose.field.none')}</option>
                      {dnsProviders.map(providerOption)}
                    </Select>
                  </Field>
                </div>

                {/* ── Additional providers ── */}
                <div className={cn('grid grid-cols-1 gap-5', formData.ui_expose_mode !== 'dns_only' && 'md:grid-cols-2')}>
                  {formData.ui_expose_mode !== 'dns_only' && (
                    <ExtraProviderList
                      label={t('expose.field.extra_proxies')}
                      providers={extraProxyCandidates}
                      selectedIds={formData.extra_proxy_provider_ids}
                      onToggle={(id, checked) => toggleExtra('proxy', id, checked)}
                      emptyText={providersUnread ? t('ui.error.list_unavailable') : t('expose.field.extra_proxies_empty')}
                    />
                  )}
                  <ExtraProviderList
                    label={t('expose.field.extra_dns')}
                    providers={extraDnsCandidates}
                    selectedIds={formData.extra_dns_provider_ids}
                    onToggle={(id, checked) => toggleExtra('dns', id, checked)}
                    emptyText={providersUnread ? t('ui.error.list_unavailable') : t('expose.field.extra_dns_empty')}
                  />
                </div>

                {/* Hidden in DNS-only mode only while the resolver is local, where the
                    record takes the service's own address. A public zone needs a target
                    stated here, and a refusal may name it. */}
                {formData.dns_provider_id && (formData.ui_expose_mode !== 'dns_only' || isExternalDns) && (
                  <div className="space-y-3 rounded-xl border border-border bg-muted/40 p-4">
                    <Field
                      label={isLocalDns ? t('expose.field.dns_target_local') : t('expose.field.dns_target_external')}
                      labelAddon={
                        <FieldHint
                          text={
                            isLocalDns
                              ? t('expose.field.dns_target_local_hint')
                              : t('expose.field.dns_target_external_hint')
                          }
                        />
                      }
                      hint={
                        isExternalDns && targetSuggestion?.recommended && !formData.dns_ip ? (
                          <>
                            {t('expose.detected')}{' '}
                            <span className="font-mono text-foreground">{targetSuggestion.recommended}</span>
                            <button
                              type="button"
                              onClick={() =>
                                setFormData((prev) => ({ ...prev, dns_ip: String(targetSuggestion.recommended) }))
                              }
                              className="ml-2 rounded-xs font-medium text-primary hover:underline focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
                            >
                              {t('expose.use_this')}
                            </button>
                          </>
                        ) : undefined
                      }
                    >
                      <div className="flex gap-2">
                        <Input
                          type="text"
                          autoComplete="off"
                          spellCheck={false}
                          className="font-mono"
                          wrapperClassName="flex-1"
                          value={formData.dns_ip}
                          onChange={(e) => setFormData((prev) => ({ ...prev, dns_ip: e.target.value }))}
                          placeholder={
                            isLocalDns
                              ? t('expose.field.dns_target_local_placeholder')
                              : t('expose.field.dns_target_external_placeholder')
                          }
                        />
                        {publicTarget.canOfferAuto && (
                          <Button
                            type="button"
                            variant="outline"
                            leftIcon={<RefreshCw />}
                            loading={isFetchingTargetSuggestion}
                            onClick={() => {
                              refetchTargetSuggestion();
                              if (targetSuggestion?.recommended) {
                                setFormData((prev) => ({ ...prev, dns_ip: String(targetSuggestion.recommended) }));
                              }
                            }}
                          >
                            {t('expose.detect')}
                          </Button>
                        )}
                      </div>
                    </Field>

                    {/* The button above used to spin, stop, and change nothing. It stands in
                        for the retry here, so neither of these carries one of its own. The
                        failure stays on screen even once a target is typed by hand, because
                        the automatic update switch below reads the same lookup; "nothing was
                        detected" goes away, since a typed target settles that question. */}
                    {publicTarget.canOfferAuto && targetLookupFailed && (
                      <InlineAlert tone="warning" title={t('expose.detect_unread')}>
                        {t('expose.detect_unread_hint')}
                      </InlineAlert>
                    )}
                    {publicTarget.canOfferAuto && targetLookupFoundNothing && !formData.dns_ip && (
                      <InlineAlert tone="info" title={t('expose.detect_none')} />
                    )}

                    {/* Auto-update DNS — only for external DNS with auto capability */}
                    {publicTarget.canOfferAuto && (
                      <Switch
                        size="sm"
                        checked={publicTarget.autoUpdateDns}
                        onCheckedChange={(checked) =>
                          setFormData((prev) => ({ ...prev, public_target_mode: 'auto', auto_update_dns: checked }))
                        }
                        label={t('expose.field.auto_update_dns')}
                        description={t('expose.field.auto_update_dns_hint')}
                      />
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </section>

      {/* ── Section 4: Organisation ── */}
      <section className="space-y-4">
        <SectionHeading
          as="h3"
          size="sm"
          title={t('expose.section.organize.title')}
          description={t('expose.section.organize.description')}
        />
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
          <TaxonomyChips
            label={t('expose.field.tags')}
            items={tags}
            selected={formData.tag_ids}
            onToggle={toggleTag}
            loading={isLoadingTaxonomy}
            emptyText={
              tagsError ? (
                t('ui.error.list_unavailable')
              ) : (
                <>
                  {t('expose.field.tags_empty')}{' '}
                  <Link to="/settings?tab=tags" className="font-medium text-primary hover:underline">
                    {t('expose.field.manage_taxonomy')}
                  </Link>
                </>
              )
            }
          />
          <TaxonomyChips
            label={t('expose.field.environments')}
            items={environments}
            selected={formData.environment_ids}
            onToggle={toggleEnvironment}
            loading={isLoadingTaxonomy}
            emptyText={
              environmentsError ? (
                t('ui.error.list_unavailable')
              ) : (
                <>
                  {t('expose.field.environments_empty')}{' '}
                  <Link to="/settings?tab=environments" className="font-medium text-primary hover:underline">
                    {t('expose.field.manage_taxonomy')}
                  </Link>
                </>
              )
            }
          />
        </div>
      </section>
    </div>
  );
}
