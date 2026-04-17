import type { SyncResult, SyncProxyHost, SyncDnsRewrite } from '@/types/api';
import type { ProviderFormState, ProviderValidationResult, ProviderTypeMeta } from '@/components/features/providers/providerConstants';

export type StepName = 'welcome' | 'restore' | 'password' | 'providers' | 'provider-form' | 'notifications' | 'docker' | 'import' | 'done';

export interface ProviderItem {
  id: number;
  name: string;
  type: string;
}

export interface ImportableService {
  kind: 'proxy' | 'dns';
  source: string;
  type: string;
  name: string;
  domain?: string;
  target?: string;
  selected?: boolean;
  raw: SyncProxyHost | SyncDnsRewrite;
}

export type { ProviderFormState, ProviderValidationResult, ProviderTypeMeta, SyncResult };
