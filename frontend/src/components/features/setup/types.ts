import type { ProviderType, SyncResult } from '@/types/api';
import type { ProviderFormState, ProviderValidationResult, ProviderTypeMeta } from '@/components/features/providers/providerConstants';

export type StepName = 'welcome' | 'restore' | 'password' | 'providers' | 'provider-form' | 'notifications' | 'docker' | 'import' | 'done';

/** The three columns of a `GET /api/providers` row the wizard steps actually read. */
export interface ProviderItem {
  id: number;
  name: string;
  type: ProviderType;
}

export type { ProviderFormState, ProviderValidationResult, ProviderTypeMeta, SyncResult };
