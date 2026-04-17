import { Dispatch, SetStateAction } from 'react';
import { Loader2, RefreshCw, Server } from 'lucide-react';
import toast from 'react-hot-toast';
import type { FormState, Provider } from './types';
import { hasCapability } from './types';
import { FieldHint } from '@/components/ui/FieldHint';

interface TargetSuggestion {
  candidates: Array<{ value: string; source: string }>;
  recommended: string;
}

interface ServiceFormProps {
  formData: FormState;
  setFormData: Dispatch<SetStateAction<FormState>>;
  providers: Provider[];
  domains: string[];
  isLoadingProviders: boolean;
  isLoadingDomains: boolean;
  providerTypeMap: Record<string, Record<string, unknown>>;
  targetSuggestion: TargetSuggestion | undefined;
  isFetchingTargetSuggestion: boolean;
  refetchTargetSuggestion: () => void;
}

export function ServiceForm({
  formData,
  setFormData,
  providers,
  domains,
  isLoadingProviders,
  isLoadingDomains,
  providerTypeMap,
  targetSuggestion,
  isFetchingTargetSuggestion,
  refetchTargetSuggestion,
}: ServiceFormProps) {

  const allProviders = providers.filter((p) => Boolean(p.enabled));
  const proxyProviders = allProviders.filter((p) => hasCapability(p, 'proxy', providerTypeMap));
  const dnsProviders = allProviders.filter((p) => hasCapability(p, 'dns', providerTypeMap));
  const tunnelProviders = proxyProviders.filter(
    (p) =>
      hasCapability(p, 'supports_tunnel', providerTypeMap) ||
      (p.type || '').toLowerCase() === 'cloudflare_tunnel',
  );
  const hasTunnelProvider = tunnelProviders.length > 0;
  // Standard (non-tunnel) proxy providers — used for "Primary reverse proxy" in proxy_dns
  // mode and for "additional reverse providers" in both modes.
  const tunnelIds = new Set(tunnelProviders.map((p) => p.id));
  const standardProxyProviders = proxyProviders.filter((p) => !tunnelIds.has(p.id));

  const selectedDns = dnsProviders.find((p) => String(p.id) === formData.dns_provider_id);
  const selectedDnsSupportsAuto = selectedDns
    ? hasCapability(selectedDns, 'supports_auto_public_target', providerTypeMap)
    : false;

  // Local DNS providers resolve internally (LAN IP); external ones resolve publicly (WAN IP)
  const LOCAL_DNS_TYPES = ['pihole', 'adguard'];
  const isLocalDns = selectedDns ? LOCAL_DNS_TYPES.includes((selectedDns.type || '').toLowerCase()) : false;
  const isExternalDns = selectedDns && !isLocalDns;

  const effectivePublicTargetMode =
    formData.public_target_mode === 'auto' && formData.dns_provider_id && !selectedDnsSupportsAuto
      ? 'manual'
      : formData.public_target_mode;

  const effectiveAutoUpdateDns =
    effectivePublicTargetMode === 'auto' ? formData.auto_update_dns : false;

  const fqdnPreview =
    formData.subdomain && formData.domain
      ? `${formData.subdomain}.${formData.domain}`
      : 'subdomain.domain.tld';

  // Auto-sync tunnel_hostname when subdomain/domain change in tunnel mode.
  // Only auto-fill when the user hasn't typed a custom hostname.
  const prevFqdn = `${formData.subdomain}.${formData.domain}`;
  const tunnelHostnameIsDefault =
    !formData.tunnel_hostname || formData.tunnel_hostname === prevFqdn;

  const parseTargetInput = (value: string) => {
    const trimmed = value.trim();
    if (!trimmed || !/^https?:\/\//i.test(trimmed)) {
      setFormData((prev) => ({ ...prev, target_ip: value }));
      return;
    }
    try {
      const parsed = new URL(trimmed);
      const parsedPort = parsed.port
        ? Number(parsed.port)
        : parsed.protocol === 'https:'
          ? 443
          : 80;
      setFormData((prev) => ({
        ...prev,
        target_ip: parsed.hostname,
        target_port: Number.isFinite(parsedPort) ? parsedPort : prev.target_port,
        forward_scheme: parsed.protocol === 'https:' ? 'https' : 'http',
      }));
      toast.success('Target parsed from URL');
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

  return (
    <>
      {/* ── Section 1: Public Route ── */}
      <div>
        <h3 className="text-sm font-bold text-foreground uppercase tracking-widest mb-4">
          1. Public Route
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="block text-xs font-semibold text-muted-foreground tracking-wide">
              Subdomain
            </label>
            <input
              type="text"
              required
              value={formData.subdomain}
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
              placeholder="app"
              className="w-full bg-card border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-medium text-foreground placeholder:text-muted-foreground outline-none transition-all shadow-sm font-mono"
            />
            <p className="text-xs text-muted-foreground">
              Final route: <span className="font-mono">{fqdnPreview}</span>
            </p>
          </div>

          <div className="space-y-1.5">
            <label className="block text-xs font-semibold text-muted-foreground tracking-wide">
              Base Domain
            </label>
            <select
              value={formData.domain}
              onChange={(e) => {
                const dom = e.target.value;
                setFormData((prev) => {
                  const next: FormState = { ...prev, domain: dom };
                  if (prev.expose_mode === 'tunnel' && tunnelHostnameIsDefault && prev.subdomain && dom) {
                    next.tunnel_hostname = `${prev.subdomain}.${dom}`;
                  }
                  return next;
                });
              }}
              className="w-full bg-card border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm text-foreground outline-none transition-all shadow-sm"
              disabled={isLoadingDomains}
              required
            >
              <option value="">Select a registered domain</option>
              {(domains || []).map((domain) => (
                <option key={domain} value={domain}>
                  {domain}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">
              Need a new domain? Add it in{' '}
              <a href="/settings" className="text-primary hover:text-primary">
                Settings
              </a>{' '}
              first.
            </p>
          </div>
        </div>

        <div className="mt-4 rounded-lg border border-border bg-muted/30 p-3">
          <p className="text-xs font-semibold text-muted-foreground mb-2">Exposure mode</p>
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2 text-sm text-foreground">
              <input
                type="radio"
                checked={formData.expose_mode === 'proxy_dns'}
                onChange={() =>
                  setFormData((prev) => ({
                    ...prev,
                    expose_mode: 'proxy_dns',
                    tunnel_provider_id: '',
                    tunnel_hostname: '',
                    extra_proxy_provider_ids: [],
                  }))
                }
              />
              DNS + reverse proxy
            </label>
            <label className={`flex items-center gap-2 text-sm ${hasTunnelProvider ? 'text-foreground' : 'text-muted-foreground cursor-not-allowed'}`}>
              <input
                type="radio"
                checked={formData.expose_mode === 'tunnel'}
                disabled={!hasTunnelProvider}
                onChange={() =>
                  setFormData((prev) => ({
                    ...prev,
                    expose_mode: 'tunnel',
                    public_target_mode: 'manual',
                    auto_update_dns: false,
                    dns_provider_id: '',
                    dns_ip: '',
                    tunnel_hostname: prev.tunnel_hostname || fqdnPreview,
                  }))
                }
              />
              Cloudflare Tunnel
              {!hasTunnelProvider && (
                <span className="ml-1 text-xs text-muted-foreground">
                  — No tunnel provider configured.{' '}
                  <a href="/providers" className="text-primary hover:underline">Add one in Integrations</a>
                </span>
              )}
            </label>
          </div>
        </div>
      </div>

      {/* ── Section 2: Internal Destination ── */}
      <div>
        <h3 className="text-sm font-bold text-foreground uppercase tracking-widest mb-4">
          2. Internal Destination
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="md:col-span-2 space-y-1.5">
            <label className="block text-xs font-semibold text-muted-foreground">
              Internal target (IP or hostname)
            </label>
            <div className="relative">
              <Server className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                required
                value={formData.target_ip}
                onChange={(e) => parseTargetInput(e.target.value)}
                placeholder="http://192.168.1.100:3000 or 192.168.1.100"
                className="w-full bg-card border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg pl-9 pr-4 py-2.5 text-sm font-medium text-foreground outline-none transition-all shadow-sm font-mono"
              />
            </div>
            <p className="text-xs text-muted-foreground">
              The internal address of your service. Paste a full URL to auto-fill scheme, host and port.
            </p>
          </div>

          <div className="space-y-1.5">
            <label className="block text-xs font-semibold text-muted-foreground">Port</label>
            <input
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              required
              value={formData.target_port === 0 ? '' : formData.target_port}
              onChange={(e) => {
                const raw = e.target.value.replace(/\D/g, '');
                const num = raw === '' ? 0 : Math.min(65535, Number(raw));
                setFormData((prev) => ({ ...prev, target_port: num }));
              }}
              onBlur={() => {
                if (!formData.target_port) setFormData((prev) => ({ ...prev, target_port: 80 }));
              }}
              className="w-full bg-card border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-bold text-primary outline-none transition-all shadow-sm text-center font-mono"
            />
          </div>

          <div className="space-y-1.5">
            <label className="block text-xs font-semibold text-muted-foreground">
              Forward scheme
            </label>
            <select
              value={formData.forward_scheme}
              onChange={(e) =>
                setFormData((prev) => ({
                  ...prev,
                  forward_scheme: e.target.value as 'http' | 'https',
                }))
              }
              className="w-full bg-card border border-border focus:border-primary rounded-lg px-4 py-2.5 text-sm shadow-sm"
            >
              <option value="http">http</option>
              <option value="https">https</option>
            </select>
          </div>
        </div>

        <div className="mt-3 flex items-center gap-6">
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={formData.websocket}
              onChange={(e) => setFormData((prev) => ({ ...prev, websocket: e.target.checked }))}
            />
            WebSocket enabled
          </label>
          <p className="text-xs text-muted-foreground">
            Enable only if the backend app requires WebSocket upgrades (e.g.
            dashboards/terminals/live events).
          </p>
        </div>
      </div>

      {/* ── Section 3: Provider Strategy ── */}
      <div>
        <h3 className="text-sm font-bold text-foreground uppercase tracking-widest mb-4">
          3. Provider Strategy
        </h3>
        {isLoadingProviders ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground p-4 border border-border rounded-lg bg-muted animate-pulse">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading providers...
          </div>
        ) : (
          <div className="space-y-5">
            {formData.expose_mode === 'tunnel' && (
              <>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  <div className="space-y-1.5">
                    <label className="block text-xs font-semibold text-muted-foreground">
                      Tunnel provider
                    </label>
                    <select
                      value={formData.tunnel_provider_id}
                      onChange={(e) => {
                        const nextId = e.target.value;
                        setFormData((prev) => ({
                          ...prev,
                          tunnel_provider_id: nextId,
                          extra_proxy_provider_ids: prev.extra_proxy_provider_ids.filter(
                            (id) => id !== nextId,
                          ),
                        }));
                      }}
                      className="w-full bg-card border border-border focus:border-primary rounded-lg px-3 py-2.5 text-sm shadow-sm"
                      disabled={tunnelProviders.length === 0}
                    >
                      <option value="">Select Cloudflare Tunnel provider</option>
                      {tunnelProviders.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} ({p.type})
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="space-y-1.5">
                    <label className="block text-xs font-semibold text-muted-foreground">
                      Public tunnel hostname
                    </label>
                    <input
                      type="text"
                      value={formData.tunnel_hostname}
                      onChange={(e) =>
                        setFormData((prev) => ({
                          ...prev,
                          tunnel_hostname: e.target.value.trim().toLowerCase(),
                        }))
                      }
                      placeholder={fqdnPreview}
                      className="w-full bg-card border border-border focus:border-primary rounded-lg px-4 py-2.5 text-sm shadow-sm font-mono"
                    />
                    <p className="text-xs text-muted-foreground">
                      {tunnelHostnameIsDefault
                        ? <>Auto-filled from your public route above. Edit only if the tunnel ingress hostname differs.</>
                        : <>Custom hostname \u2014 clear to revert to auto-fill ({fqdnPreview}).</>}
                    </p>
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="block text-xs font-semibold text-muted-foreground">
                    Push to additional reverse proxy providers
                  </label>
                  <div className="max-h-36 overflow-auto border border-border rounded-lg p-3 space-y-2">
                    {standardProxyProviders.map((p) => (
                        <label key={p.id} className="flex items-center gap-2 text-sm text-foreground">
                          <input
                            type="checkbox"
                            checked={formData.extra_proxy_provider_ids.includes(String(p.id))}
                            onChange={(e) => toggleExtra('proxy', String(p.id), e.target.checked)}
                          />
                          {p.name} ({p.type})
                        </label>
                      ))}
                    {standardProxyProviders.length === 0 && (
                      <p className="text-xs text-muted-foreground">
                        No additional proxy provider available.
                      </p>
                    )}
                  </div>
                </div>
              </>
            )}

            {formData.expose_mode === 'proxy_dns' && (
              <>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  <div className="space-y-1.5">
                    <label className="block text-xs font-semibold text-muted-foreground">
                      Primary reverse proxy
                    </label>
                    <select
                      value={formData.proxy_provider_id}
                      onChange={(e) => {
                        const nextProxyId = e.target.value;
                        const selectedProvider = standardProxyProviders.find(
                          (p) => String(p.id) === nextProxyId,
                        );
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
                          dns_ip: prev.dns_ip.trim() ? prev.dns_ip : inferredDnsTarget,
                          extra_proxy_provider_ids: prev.extra_proxy_provider_ids.filter(
                            (id) => id !== nextProxyId,
                          ),
                        }));
                      }}
                      className="w-full bg-card border border-border focus:border-primary rounded-lg px-3 py-2.5 text-sm shadow-sm"
                      disabled={standardProxyProviders.length === 0}
                    >
                      <option value="">None</option>
                      {standardProxyProviders.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} ({p.type})
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="space-y-1.5">
                    <label className="block text-xs font-semibold text-muted-foreground">
                      Primary DNS provider
                    </label>
                    <select
                      value={formData.dns_provider_id}
                      onChange={(e) => {
                        const nextDnsProviderId = e.target.value;
                        const nextDnsProvider = dnsProviders.find(
                          (p) => String(p.id) === nextDnsProviderId,
                        );
                        const nextSupportsAuto = nextDnsProvider
                          ? hasCapability(
                              nextDnsProvider,
                              'supports_auto_public_target',
                              providerTypeMap,
                            )
                          : false;

                        // Clear dns_ip when scope changes (local ↔ external) to avoid a stale
                        // LAN IP sitting in a field now labelled "Public WAN IP" and vice-versa.
                        const prevIsLocal = LOCAL_DNS_TYPES.includes((selectedDns?.type || '').toLowerCase());
                        const nextIsLocal = LOCAL_DNS_TYPES.includes(((nextDnsProvider?.type) || '').toLowerCase());
                        const scopeChanged = nextDnsProviderId && prevIsLocal !== nextIsLocal;

                        setFormData((prev) => ({
                          ...prev,
                          dns_provider_id: nextDnsProviderId,
                          dns_ip: scopeChanged ? '' : prev.dns_ip,
                          public_target_mode:
                            nextDnsProviderId && !nextSupportsAuto
                              ? 'manual'
                              : prev.public_target_mode,
                          auto_update_dns:
                            nextDnsProviderId && !nextSupportsAuto ? false : prev.auto_update_dns,
                          extra_dns_provider_ids: prev.extra_dns_provider_ids.filter(
                            (id) => id !== nextDnsProviderId,
                          ),
                        }));
                      }}
                      className="w-full bg-card border border-border focus:border-primary rounded-lg px-3 py-2.5 text-sm shadow-sm"
                      disabled={dnsProviders.length === 0}
                    >
                      <option value="">None</option>
                      {dnsProviders.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} ({p.type})
                        </option>
                      ))}
                    </select>
                    {!formData.proxy_provider_id && !formData.dns_provider_id && (
                      <p className="text-xs text-yellow-600 dark:text-yellow-400 mt-1">
                        At least one provider (proxy or DNS) is required.
                      </p>
                    )}
                    {/* Scope badge — shown once a DNS provider is selected */}
                    {selectedDns && (
                      <p className={`text-xs font-semibold mt-1 ${isLocalDns ? 'text-teal-600 dark:text-teal-400' : 'text-orange-600 dark:text-orange-400'}`}>
                        {isLocalDns
                          ? 'Local DNS — resolves on your LAN only (Pi-hole / AdGuard). Use your reverse proxy\'s LAN IP as the target.'
                          : 'External DNS — resolves on the public internet (Cloudflare, etc.). Use your public WAN IP as the target. Ensure ports 80/443 are forwarded to your reverse proxy.'}
                      </p>
                    )}
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  <div className="space-y-2">
                    <label className="block text-xs font-semibold text-muted-foreground">
                      Push to additional reverse proxy providers
                    </label>
                    <div className="max-h-36 overflow-auto border border-border rounded-lg p-3 space-y-2">
                      {standardProxyProviders
                        .filter((p) => String(p.id) !== formData.proxy_provider_id)
                        .map((p) => (
                          <label
                            key={p.id}
                            className="flex items-center gap-2 text-sm text-foreground"
                          >
                            <input
                              type="checkbox"
                              checked={formData.extra_proxy_provider_ids.includes(String(p.id))}
                              onChange={(e) => toggleExtra('proxy', String(p.id), e.target.checked)}
                            />
                            {p.name} ({p.type})
                          </label>
                        ))}
                      {standardProxyProviders.filter((p) => String(p.id) !== formData.proxy_provider_id)
                        .length === 0 && (
                        <p className="text-xs text-muted-foreground">
                          No additional proxy provider available.
                        </p>
                      )}
                    </div>
                  </div>

                  <div className="space-y-2">
                    <label className="block text-xs font-semibold text-muted-foreground">
                      Push to additional DNS providers
                    </label>
                    <div className="max-h-36 overflow-auto border border-border rounded-lg p-3 space-y-2">
                      {dnsProviders
                        .filter((p) => String(p.id) !== formData.dns_provider_id)
                        .map((p) => (
                          <label
                            key={p.id}
                            className="flex items-center gap-2 text-sm text-foreground"
                          >
                            <input
                              type="checkbox"
                              checked={formData.extra_dns_provider_ids.includes(String(p.id))}
                              onChange={(e) => toggleExtra('dns', String(p.id), e.target.checked)}
                            />
                            {p.name} ({p.type})
                          </label>
                        ))}
                      {dnsProviders.filter((p) => String(p.id) !== formData.dns_provider_id)
                        .length === 0 && (
                        <p className="text-xs text-muted-foreground">
                          No additional DNS provider available.
                        </p>
                      )}
                    </div>
                  </div>
                </div>

                {formData.dns_provider_id && (
                  <div className="space-y-3 rounded-lg border border-border bg-muted/30 p-3">
                    {/* Target IP field — label and hint adapt to local vs external */}
                    <div className="space-y-1.5">
                      <label className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
                        {isLocalDns ? 'Reverse proxy LAN IP' : 'DNS public target (WAN IP)'}
                        <FieldHint text={
                          isLocalDns
                            ? 'The LAN IP of your reverse proxy (e.g. 192.168.1.10). Pi-hole / AdGuard will create a local DNS record pointing to it.'
                            : 'Your public WAN IP. Cloudflare will create an A record pointing to it. Your router must forward ports 80/443 to your reverse proxy.'
                        } />
                      </label>
                      <div className="flex gap-2">
                        <input
                          type="text"
                          value={formData.dns_ip}
                          onChange={(e) => setFormData((prev) => ({ ...prev, dns_ip: e.target.value }))}
                          placeholder={isLocalDns ? '192.168.1.10 (LAN IP of your proxy)' : '203.0.113.1'}
                          className="flex-1 bg-card border border-border focus:border-primary rounded-lg px-4 py-2.5 text-sm shadow-sm font-mono"
                        />
                        {isExternalDns && selectedDnsSupportsAuto && (
                          <button
                            type="button"
                            onClick={() => {
                              refetchTargetSuggestion();
                              if (targetSuggestion?.recommended) {
                                setFormData((prev) => ({ ...prev, dns_ip: String(targetSuggestion.recommended) }));
                              }
                            }}
                            className="inline-flex items-center gap-1.5 px-3 py-2.5 text-xs rounded-lg border border-border hover:bg-accent whitespace-nowrap"
                          >
                            <RefreshCw className={`w-3.5 h-3.5 ${isFetchingTargetSuggestion ? 'animate-spin' : ''}`} />
                            Detect
                          </button>
                        )}
                      </div>
                      {isExternalDns && targetSuggestion?.recommended && !formData.dns_ip && (
                        <p className="text-xs text-muted-foreground">
                          Detected: <span className="font-mono text-foreground">{targetSuggestion.recommended}</span>
                          <button
                            type="button"
                            onClick={() => setFormData((prev) => ({ ...prev, dns_ip: String(targetSuggestion.recommended) }))}
                            className="ml-2 text-primary hover:underline"
                          >
                            Use this
                          </button>
                        </p>
                      )}
                    </div>

                    {/* Auto-update DNS option for external DNS */}
                    {isExternalDns && selectedDnsSupportsAuto && (
                      <label className="flex items-center gap-2 text-sm text-foreground">
                        <input
                          type="checkbox"
                          checked={effectiveAutoUpdateDns}
                          onChange={(e) =>
                            setFormData((prev) => ({
                              ...prev,
                              public_target_mode: 'auto',
                              auto_update_dns: e.target.checked,
                            }))
                          }
                        />
                        Auto-update DNS when WAN IP changes
                      </label>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </>
  );
}
