import { ArrowLeft, ArrowRight, GitMerge, Server, Plus, Trash2, CheckCircle2, Shield, type LucideIcon } from 'lucide-react';
import { ProviderLogo } from '@/components/ui/ProviderLogos';
import { fallbackIconByType as iconByType, descByType, providerColor, categoryByType } from '@/components/features/providers/providerConstants';
import type { ProviderItem } from './types';

interface ProvidersStepProps {
  providers: ProviderItem[];
  onAdd: () => void;
  onDelete: (id: number) => void;
  deleteIsPending: boolean;
  onBack: () => void;
  onContinue: () => void;
}

function renderProviderCard(provider: ProviderItem, onDelete: (id: number) => void, deleteIsPending: boolean) {
  const FallbackIcon = iconByType[provider.type] || Server;
  const color = providerColor[provider.type] || 'bg-primary/10 text-primary border-primary/20';

  return (
    <div key={provider.id} className="flex items-center gap-3 px-4 py-3 rounded-xl bg-background border border-border group hover:border-primary/30 transition-colors">
      <div className={`p-2 rounded-lg border ${color}`}>
        <ProviderLogo type={provider.type} className="w-4 h-4" fallback={<FallbackIcon className="w-4 h-4" />} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-foreground">{provider.name}</p>
        <p className="text-xs text-muted-foreground">{descByType[provider.type] || provider.type}</p>
      </div>
      <button
        onClick={() => onDelete(provider.id)}
        disabled={deleteIsPending}
        className="text-muted-foreground hover:text-destructive transition-colors opacity-0 group-hover:opacity-100 shrink-0"
        title="Remove provider"
      >
        <Trash2 size={16} />
      </button>
      <CheckCircle2 size={18} className="text-primary shrink-0" />
    </div>
  );
}

function renderProviderCategory(
  label: string,
  Icon: LucideIcon,
  providers: ProviderItem[],
  onDelete: (id: number) => void,
  deleteIsPending: boolean,
) {
  if (providers.length === 0) return null;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Icon size={16} className="text-muted-foreground" />
        <h3 className="text-sm font-semibold text-foreground">{label}</h3>
        <span className="ml-auto text-xs font-medium text-muted-foreground bg-muted px-2 py-1 rounded-full">
          {providers.length}
        </span>
      </div>
      <div className="space-y-2 pl-6 border-l border-border">
        {providers.map((provider) => renderProviderCard(provider, onDelete, deleteIsPending))}
      </div>
    </div>
  );
}

export function ProvidersStep({ providers, onAdd, onDelete, deleteIsPending, onBack, onContinue }: ProvidersStepProps) {
  // Group providers by category
  const dnsProviders = providers.filter(p => {
    const category = categoryByType[p.type];
    return category?.label?.includes('DNS') || categoryByType[p.type]?.label?.includes('Zero Trust');
  });
  
  const proxyProviders = providers.filter(p => {
    const category = categoryByType[p.type];
    return category?.label?.includes('Proxy') || (p.type === 'npm' || p.type === 'traefik');
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary grid place-items-center">
          <GitMerge size={20} />
        </div>
        <div>
          <h2 className="text-xl font-bold text-foreground">Add Integrations</h2>
          <p className="text-sm text-muted-foreground">Connect your reverse proxies and DNS providers.</p>
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-6 space-y-4">
        {providers.length === 0 ? (
          <div className="text-center py-8">
            <Server className="w-12 h-12 text-muted-foreground/30 mx-auto mb-3" />
            <p className="text-sm text-muted-foreground">No integrations added yet.</p>
            <p className="text-xs text-muted-foreground mt-1">
              Add at least one reverse proxy (NPM, Traefik) or DNS provider to get started.
            </p>
          </div>
        ) : (
          <div className="space-y-6">
            {renderProviderCategory('DNS Providers', Shield, dnsProviders, onDelete, deleteIsPending)}
            {renderProviderCategory('Reverse Proxies', Server, proxyProviders, onDelete, deleteIsPending)}
          </div>
        )}

        <button
          onClick={onAdd}
          className="w-full flex items-center justify-center gap-2 border-2 border-dashed border-border rounded-xl py-4 text-sm font-medium text-muted-foreground hover:text-foreground hover:border-primary/40 hover:bg-primary/5 transition-all"
        >
          <Plus size={18} />
          Add a provider
        </button>
      </div>

      <div className="flex justify-between">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft size={14} />
          Back
        </button>
        <button
          onClick={onContinue}
          className="inline-flex items-center gap-2 bg-primary text-primary-foreground hover:opacity-90 px-6 py-2.5 rounded-xl text-sm font-semibold transition-all"
        >
          {providers.length > 0 ? 'Continue' : 'Skip for now'}
          <ArrowRight size={16} />
        </button>
      </div>
    </div>
  );
}
