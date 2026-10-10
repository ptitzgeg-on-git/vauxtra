import { api } from '@/api/client';
import type { ServicePayload } from '@/types/api';

/**
 * Save a service from the expose form: one PUT (edit) or one POST (create), nothing else.
 *
 * The form used to follow the save with `POST /services/{id}/push` whenever extra targets
 * were listed. The backend already publishes those targets inside the same create or update
 * call, so the second request was redundant, and on a disabled service it answered 409: the
 * save had succeeded while the modal reported a failure.
 */
export function persistService(
  payload: ServicePayload,
  serviceId: number | null,
): Promise<Record<string, unknown>> {
  return serviceId
    ? api.put<Record<string, unknown>>(`/services/${serviceId}`, payload)
    : api.post<Record<string, unknown>>('/services', payload);
}
