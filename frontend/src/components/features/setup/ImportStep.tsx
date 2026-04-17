import {
  ArrowLeft, ArrowRight, Download, Server, Loader2, CheckCircle2, RefreshCw,
} from 'lucide-react';
import { ProviderLogo } from '@/components/ui/ProviderLogos';
import { fallbackIconByType as iconByType, providerColor } from '@/components/features/providers/providerConstants';
import type { ImportableService, ProviderItem } from './types';

interface ImportStepProps {
  providers: ProviderItem[];
  importableServices: ImportableService[];
  loadingImportable: boolean;
  onToggle: (index: number) => void;
  onSelectAll: () => void;
  onDeselectAll: () => void;
  onRetry: () => void;
  onImportAndFinish: () => void;
  onBack: () => void;
}

export function ImportStep({
  providers, importableServices, loadingImportable,
  onToggle, onSelectAll, onDeselectAll, onRetry, onImportAndFinish, onBack,
}: ImportStepProps) {
  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary grid place-items-center">
          <Download size={20} />
        </div>
        <div>
          <h2 className="text-xl font-bold text-foreground">Import Existing Services</h2>
          <p className="text-sm text-muted-foreground">Review services from your connected providers.</p>
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-6 space-y-5">
        {loadingImportable ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-primary" />
            <span className="ml-3 text-muted-foreground">Scanning providers...</span>
          </div>
        ) : providers.length === 0 ? (
          <div className="text-center py-8">
            <Server className="w-12 h-12 text-muted-foreground/30 mx-auto mb-3" />
            <p className="text-sm text-muted-foreground">No providers configured.</p>
            <p className="text-xs text-muted-foreground mt-1">
              Add providers first to import existing services.
            </p>
          </div>
        ) : importableServices.length === 0 ? (
          <div className="text-center py-8">
            <CheckCircle2 className="w-12 h-12 text-primary/30 mx-auto mb-3" />
            <p className="text-sm text-foreground font-medium">No services found to import</p>
            <p className="text-xs text-muted-foreground mt-1">
              Your providers don't have any existing services, or Vauxtra couldn't read them.
            </p>
            <button onClick={onRetry} className="mt-4 inline-flex items-center gap-2 text-sm text-primary hover:underline">
              <RefreshCw size={14} /> Retry scan
            </button>
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                Found <strong>{importableServices.length}</strong> service{importableServices.length > 1 ? 's' : ''} across your providers.
              </p>
              <div className="flex items-center gap-2">
                <button onClick={onSelectAll} className="text-xs text-primary hover:underline">Select all</button>
                <span className="text-muted-foreground">|</span>
                <button onClick={onDeselectAll} className="text-xs text-muted-foreground hover:text-foreground">Deselect all</button>
              </div>
            </div>

            <div className="space-y-2 max-h-80 overflow-y-auto">
              {importableServices.map((svc, idx) => {
                const FallbackIcon = iconByType[svc.type] || Server;
                const color = providerColor[svc.type] || 'bg-primary/10 text-primary border-primary/20';
                return (
                  <button
                    key={idx}
                    onClick={() => onToggle(idx)}
                    className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl border text-left transition-all ${
                      svc.selected
                        ? 'bg-primary/5 border-primary/30'
                        : 'bg-background border-border hover:border-primary/20'
                    }`}
                  >
                    <div className={`w-5 h-5 rounded border-2 flex items-center justify-center transition-colors ${
                      svc.selected ? 'bg-primary border-primary' : 'border-muted-foreground/30'
                    }`}>
                      {svc.selected && <CheckCircle2 size={12} className="text-primary-foreground" />}
                    </div>
                    <div className={`p-1.5 rounded-lg border ${color}`}>
                      <ProviderLogo type={svc.type} className="w-3.5 h-3.5" fallback={<FallbackIcon className="w-3.5 h-3.5" />} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-foreground truncate">{svc.domain || svc.name}</p>
                      <p className="text-xs text-muted-foreground truncate">
                        {svc.source} {svc.target ? `→ ${svc.target}` : ''}
                      </p>
                    </div>
                  </button>
                );
              })}
            </div>

            <div className="pt-2 border-t border-border">
              <p className="text-xs text-muted-foreground">
                Selected services will be visible in Vauxtra for monitoring. You can import more services later from the Services page.
              </p>
            </div>
          </>
        )}
      </div>

      <div className="flex justify-between">
        <button onClick={onBack} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
          <ArrowLeft size={14} /> Back
        </button>
        <button onClick={onImportAndFinish} className="inline-flex items-center gap-2 bg-primary text-primary-foreground hover:opacity-90 px-6 py-2.5 rounded-xl text-sm font-semibold transition-all">
          {importableServices.some(s => s.selected)
            ? `Import ${importableServices.filter(s => s.selected).length} & finish`
            : 'Skip & finish'}
          <ArrowRight size={16} />
        </button>
      </div>
    </div>
  );
}
