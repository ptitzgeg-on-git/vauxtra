import { Server, Container } from 'lucide-react';
import { ProviderLogo } from '@/components/ui/ProviderLogos';
import {
  type ProviderTypeMap,
  fallbackIconByType,
  descByType,
  providerColor,
} from '@/components/features/providers/providerConstants';

interface GroupedProviders {
  category: string;
  providers: Array<[string, ProviderTypeMap[string]]>;
}

interface StepTypeSelectorProps {
  groupedProviders: GroupedProviders[];
  selectedType: string;
  isDockerMode: boolean;
  onChooseProvider: (type: string, label: string, placeholderUrl: string) => void;
  onChooseDocker: () => void;
}

export function StepTypeSelector({
  groupedProviders,
  selectedType,
  isDockerMode,
  onChooseProvider,
  onChooseDocker,
}: StepTypeSelectorProps) {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-[15px] font-bold text-foreground mb-1">Select Integration Type</h3>
        <p className="text-sm text-muted-foreground">Choose the service you want to connect.</p>
      </div>

      <div className="space-y-5">
        {groupedProviders.map(({ category, providers: groupEntries }) => (
          <div key={category}>
            <p className="text-[11px] font-bold text-muted-foreground uppercase tracking-widest mb-2">{category}</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {groupEntries.map(([type, meta]) => {
                const FallbackIcon = fallbackIconByType[type] || Server;
                const selected = !isDockerMode && selectedType === type;
                return (
                  <button
                    key={type}
                    onClick={() => onChooseProvider(type, String(meta.label || type), String(meta.placeholder_url || ''))}
                    className={`flex items-start gap-4 p-4 rounded-xl text-left border transition-all duration-200 ${
                      selected
                        ? 'border-primary ring-1 ring-primary/20 bg-primary/10 shadow-md'
                        : 'border-border bg-card hover:border-primary/30 hover:bg-muted shadow-sm'
                    }`}
                  >
                    <div className={`p-2.5 rounded-lg border mt-0.5 flex-shrink-0 ${selected ? (providerColor[type] || 'bg-primary/10 text-primary border-primary/20') : 'bg-muted border-border text-primary'}`}>
                      <ProviderLogo type={type} className="w-6 h-6" fallback={<FallbackIcon className="w-6 h-6" />} />
                    </div>
                    <div className="min-w-0">
                      <div className={`font-semibold text-[14px] ${selected ? 'text-primary' : 'text-foreground'}`}>
                        {meta.label || type}
                      </div>
                      <div className="text-xs text-muted-foreground mt-0.5 leading-snug">
                        {descByType[type] || (meta.category === 'dns' ? 'DNS provider' : 'Proxy provider')}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        ))}

        {/* Docker Host — Container Discovery category */}
        <div>
          <p className="text-[11px] font-bold text-muted-foreground uppercase tracking-widest mb-2">Container Discovery</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <button
              onClick={onChooseDocker}
              className={`flex items-start gap-4 p-4 rounded-xl text-left border transition-all duration-200 ${
                isDockerMode
                  ? 'border-primary ring-1 ring-primary/20 bg-primary/10 shadow-md'
                  : 'border-border bg-card hover:border-primary/30 hover:bg-muted shadow-sm'
              }`}
            >
              <div className={`p-2.5 rounded-lg border mt-0.5 flex-shrink-0 ${isDockerMode ? 'bg-blue-500/10 text-blue-600 border-blue-500/30 dark:text-blue-400' : 'bg-muted border-border text-primary'}`}>
                <Container className="w-6 h-6" />
              </div>
              <div className="min-w-0">
                <div className={`font-semibold text-[14px] ${isDockerMode ? 'text-primary' : 'text-foreground'}`}>
                  Docker Host
                </div>
                <div className="text-xs text-muted-foreground mt-0.5 leading-snug">
                  Discover containers for auto-import
                </div>
              </div>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
