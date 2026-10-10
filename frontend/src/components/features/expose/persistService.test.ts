/**
 * C4: the save was followed by `POST /services/{id}/push` when extra targets were listed.
 * The backend already publishes them in the same call, and on a disabled service the push
 * answered 409, so a successful save was reported as a failure.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ServicePayload } from '@/types/api';

const { put, post } = vi.hoisted(() => ({ put: vi.fn(), post: vi.fn() }));
vi.mock('@/api/client', () => ({ api: { put, post } }));

import { persistService } from './persistService';

const payload = {
  subdomain: 'app',
  domain: 'example.com',
  enabled: false,
  extra_proxy_provider_ids: [3],
  extra_dns_provider_ids: [4],
} as unknown as ServicePayload;

describe('persistService', () => {
  beforeEach(() => {
    put.mockReset().mockResolvedValue({ id: 7, errors: [] });
    post.mockReset().mockResolvedValue({ id: 8, errors: [] });
  });

  it('edits with one PUT and never calls the push route', async () => {
    await expect(persistService(payload, 7)).resolves.toEqual({ id: 7, errors: [] });
    expect(put).toHaveBeenCalledTimes(1);
    expect(put).toHaveBeenCalledWith('/services/7', payload);
    expect(post).not.toHaveBeenCalled();
  });

  it('creates with one POST and never calls the push route', async () => {
    await persistService(payload, null);
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith('/services', payload);
    expect(post.mock.calls.some(([url]) => String(url).endsWith('/push'))).toBe(false);
  });
});
