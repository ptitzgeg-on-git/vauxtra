import { Loader2, RefreshCw, Trash2 } from 'lucide-react';
import { useConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { useDockerDiscovery } from '@/hooks/useDockerDiscovery';

type DiscoveryHook = ReturnType<typeof useDockerDiscovery>;

interface DockerImportProps {
  hook: DiscoveryHook;
  reverseProviders: Array<{ id: number; name: string; type: string }>;
  dnsProviders: Array<{ id: number; name: string; type: string }>;
}

export function DockerImport({ hook, reverseProviders, dnsProviders }: DockerImportProps) {
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const {
    dockerContainers,
    selectedDockerIds,
    setSelectedDockerIds,
    setDockerDomain,
    dockerProxyProviderId,
    setDockerProxyProviderId,
    dockerDnsProviderId,
    setDockerDnsProviderId,
    dockerDnsIp,
    setDockerDnsIp,
    newDockerEndpointName,
    setNewDockerEndpointName,
    newDockerEndpointHost,
    setNewDockerEndpointHost,
    setDockerEndpointId,
    dockerEndpoints,
    domains,
    effectiveEndpointId,
    selectedEndpoint,
    effectiveDomain,
    addEndpointMutation,
    testEndpointMutation,
    setDefaultEndpointMutation,
    deleteEndpointMutation,
    discoverMutation,
    importMutation,
  } = hook;

  const confidenceBadge = (confidence: string) => {
    if (confidence === 'high')
      return <span className="text-primary font-semibold text-[10px] uppercase">high</span>;
    if (confidence === 'medium')
      return <span className="text-yellow-500 font-semibold text-[10px] uppercase">med</span>;
    return <span className="text-muted-foreground font-semibold text-[10px] uppercase">low</span>;
  };

  return (
    <section className="bg-card border border-border rounded-xl shadow-sm p-6 space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-foreground">Docker Auto-Discovery</h2>
        <p className="text-sm text-muted-foreground">
          Select a Docker endpoint, discover running containers, then import selected routes.
        </p>
      </div>

      {/* Endpoint selector */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <div className="lg:col-span-2 space-y-1">
          <label className="text-xs text-muted-foreground">Docker endpoint</label>
          <select
            value={effectiveEndpointId}
            onChange={(e) => setDockerEndpointId(e.target.value)}
            className="w-full p-2 rounded-md border border-border text-sm bg-input"
          >
            {dockerEndpoints.map((ep) => (
              <option key={ep.id} value={ep.id}>
                {ep.name} ({ep.docker_host}){ep.is_default ? ' [default]' : ''}
              </option>
            ))}
          </select>
        </div>

        <div className="flex gap-2 items-end">
          <button
            onClick={() =>
              effectiveEndpointId && testEndpointMutation.mutate(effectiveEndpointId)
            }
            disabled={!effectiveEndpointId || testEndpointMutation.isPending}
            className="px-3 py-2 rounded-md border border-border text-sm hover:bg-accent disabled:opacity-60"
          >
            {testEndpointMutation.isPending ? 'Testing...' : 'Test'}
          </button>
          <button
            onClick={() =>
              effectiveEndpointId && setDefaultEndpointMutation.mutate(effectiveEndpointId)
            }
            disabled={
              !effectiveEndpointId ||
              setDefaultEndpointMutation.isPending ||
              Boolean(selectedEndpoint?.is_default)
            }
            className="px-3 py-2 rounded-md border border-border text-sm hover:bg-accent disabled:opacity-60"
          >
            Set default
          </button>
          {dockerEndpoints.length > 1 && (
            <button
              onClick={async () => {
                if (effectiveEndpointId && await confirm({
                  title: 'Delete Docker endpoint',
                  message: 'Delete this Docker endpoint?',
                  confirmLabel: 'Delete',
                  variant: 'danger',
                }))
                  deleteEndpointMutation.mutate(effectiveEndpointId);
              }}
              disabled={!effectiveEndpointId || deleteEndpointMutation.isPending}
              className="px-2 py-2 rounded-md border border-destructive/30 text-destructive text-sm hover:bg-destructive/5 disabled:opacity-60"
              title="Delete endpoint"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {/* Add endpoint form */}
      <div className="space-y-2 bg-muted/30 border border-border rounded-lg p-3">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <input
            value={newDockerEndpointName}
            onChange={(e) => setNewDockerEndpointName(e.target.value)}
            className="w-full p-2 rounded-md border border-border text-sm bg-input"
            placeholder="Endpoint name"
          />
          <input
            value={newDockerEndpointHost}
            onChange={(e) => setNewDockerEndpointHost(e.target.value)}
            className="w-full p-2 rounded-md border border-border text-sm bg-input font-mono"
            placeholder="unix:///var/run/docker.sock"
          />
          <button
            onClick={() => addEndpointMutation.mutate()}
            disabled={
              addEndpointMutation.isPending ||
              !newDockerEndpointName.trim() ||
              !newDockerEndpointHost.trim()
            }
            className="px-3 py-2 rounded-md bg-primary text-primary-foreground text-sm hover:opacity-90 disabled:opacity-60"
          >
            {addEndpointMutation.isPending ? 'Adding...' : 'Add endpoint'}
          </button>
        </div>
        <p className="text-[11px] text-muted-foreground leading-relaxed">
          <span className="font-semibold text-foreground">Local</span> <code className="bg-muted px-1 rounded font-mono">unix:///var/run/docker.sock</code>
          <span className="mx-1.5">·</span>
          <span className="font-semibold text-foreground">TCP</span> <code className="bg-muted px-1 rounded font-mono">tcp://host:2375</code> (<code className="bg-muted px-1 rounded font-mono">:2376</code> TLS)
          <span className="mx-1.5">·</span>
          <span className="font-semibold text-foreground">SSH</span> <code className="bg-muted px-1 rounded font-mono">ssh://user@host</code>
        </p>
      </div>

      {/* Endpoint chips */}
      {dockerEndpoints.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {dockerEndpoints.map((ep) => (
            <div
              key={ep.id}
              className="inline-flex items-center gap-2 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs"
            >
              <span className="font-semibold text-foreground">{ep.name}</span>
              <span className="text-muted-foreground font-mono">{ep.docker_host}</span>
              {ep.is_default && <span className="text-primary">default</span>}
              <button
                onClick={() => deleteEndpointMutation.mutate(String(ep.id))}
                disabled={deleteEndpointMutation.isPending || dockerEndpoints.length <= 1}
                className="text-muted-foreground hover:text-destructive disabled:opacity-40"
                title="Delete endpoint"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Discover button */}
      <div className="flex flex-wrap gap-3">
        <button
          onClick={() => discoverMutation.mutate()}
          disabled={discoverMutation.isPending || !effectiveEndpointId}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90 disabled:opacity-60"
        >
          {discoverMutation.isPending ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <RefreshCw className="w-4 h-4" />
          )}
          {discoverMutation.isPending ? 'Discovering...' : 'Discover containers'}
        </button>

        {dockerContainers.length > 0 && (
          <>
            <button
              onClick={() =>
                setSelectedDockerIds(
                  dockerContainers.filter((c) => c.target_port !== null && !c.existing_service).map((c) => c.id),
                )
              }
              className="px-3 py-2 text-xs rounded-md border border-border hover:bg-accent"
            >
              Select all
            </button>
            <button
              onClick={() => setSelectedDockerIds([])}
              className="px-3 py-2 text-xs rounded-md border border-border hover:bg-accent"
            >
              Clear
            </button>
          </>
        )}
      </div>

      {/* Import config */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Target domain</label>
          <input
            list="docker-domain-list"
            value={effectiveDomain}
            onChange={(e) => setDockerDomain(e.target.value)}
            className="w-full p-2 rounded-md border border-border text-sm bg-input"
            placeholder="example.com"
          />
          <datalist id="docker-domain-list">
            {domains.map((d) => (
              <option key={d} value={d} />
            ))}
          </datalist>
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Reverse provider (optional)</label>
          <select
            value={dockerProxyProviderId}
            onChange={(e) => setDockerProxyProviderId(e.target.value)}
            className="w-full p-2 rounded-md border border-border text-sm bg-input"
          >
            <option value="">None</option>
            {reverseProviders.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.type})
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">DNS provider (optional)</label>
          <select
            value={dockerDnsProviderId}
            onChange={(e) => setDockerDnsProviderId(e.target.value)}
            className="w-full p-2 rounded-md border border-border text-sm bg-input"
          >
            <option value="">None</option>
            {dnsProviders.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.type})
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">DNS public target (optional)</label>
          <input
            value={dockerDnsIp}
            onChange={(e) => setDockerDnsIp(e.target.value)}
            className="w-full p-2 rounded-md border border-border text-sm bg-input"
            placeholder="192.168.1.1"
          />
        </div>
      </div>

      {/* Container list */}
      {dockerContainers.length > 0 && (
        <div className="rounded-md border border-border overflow-hidden">
          <div className="max-h-64 overflow-y-auto divide-y divide-border/70">
            {dockerContainers.map((container) => {
              const selected = selectedDockerIds.includes(container.id);
              const alreadyConfigured = !!container.existing_service;
              const disabled = container.target_port === null || alreadyConfigured;
              const suggestion = container.suggestion;
              return (
                <label
                  key={container.id}
                  className={`flex items-center justify-between gap-3 p-3 text-sm cursor-pointer hover:bg-muted/40 ${disabled ? 'opacity-50' : ''}`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <input
                      type="checkbox"
                      checked={selected}
                      disabled={disabled}
                      onChange={(e) =>
                        setSelectedDockerIds((prev) =>
                          e.target.checked
                            ? [...prev, container.id]
                            : prev.filter((id) => id !== container.id),
                        )
                      }
                    />
                    <div className="min-w-0">
                      <p className="font-medium truncate text-foreground">{container.name}</p>
                      <p className="text-xs text-muted-foreground truncate">
                        {container.target_ip}:{container.target_port ?? 'no port'} →{' '}
                        {suggestion?.subdomain ?? container.suggested_subdomain}.
                        {effectiveDomain || '<domain>'}
                        {suggestion?.middlewares?.length ? (
                          <span className="ml-1 text-muted-foreground">
                            [{suggestion.middlewares.join(', ')}]
                          </span>
                        ) : null}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {alreadyConfigured ? (
                      <span className="text-primary font-semibold text-[10px] uppercase" title={`Matches ${container.existing_service?.fqdn}`}>
                        configured
                      </span>
                    ) : (
                      suggestion && confidenceBadge(suggestion.confidence)
                    )}
                    <span className="text-xs text-muted-foreground truncate max-w-36">
                      {container.image}
                    </span>
                  </div>
                </label>
              );
            })}
          </div>
        </div>
      )}

      {/* Import button */}
      <button
        onClick={() => importMutation.mutate()}
        disabled={
          importMutation.isPending || selectedDockerIds.length === 0 || !effectiveDomain
        }
        className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90 disabled:opacity-60"
      >
        {importMutation.isPending
          ? 'Importing...'
          : `Import selected (${selectedDockerIds.length})`}
      </button>

      {ConfirmDialogElement}
    </section>
  );
}
