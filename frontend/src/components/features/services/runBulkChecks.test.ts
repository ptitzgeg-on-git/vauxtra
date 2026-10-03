/**
 * The bulk "Check now" loop, and when it gives up.
 *
 * Before this, a run over fifty services with an expired session sent fifty requests, got
 * fifty 401s, and reported fifty services down. Same with the network gone. Those two
 * failures say nothing about the service being checked, so the loop stops on the first one.
 * A 404 or a 500 is about that one service and is still counted against it.
 */

import { describe, expect, it, vi } from 'vitest';
import { HttpError } from '@/api/httpError';
import type { Service } from '@/types/api';
import { runBulkChecks } from './helpers';

type Result = { tested?: boolean; status?: string };

const svc = (id: number) => ({ id }) as Service;
const TARGETS = [svc(1), svc(2), svc(3), svc(4)];

const httpError = (status: number): HttpError =>
  new HttpError(`Request failed with status code ${status}`, '/services/1/check', { status, data: {}, headers: {} });
const networkError = (): HttpError => new HttpError('Network Error', '/services/1/check');

describe('runBulkChecks', () => {
  it('counts every outcome when nothing goes wrong', async () => {
    const answers: Result[] = [{ status: 'ok' }, { status: 'error' }, { tested: false, status: 'unknown' }, { status: 'ok' }];
    const check = vi.fn((s: Service) => Promise.resolve(answers[s.id - 1]));
    const run = await runBulkChecks(TARGETS, check, { signal: new AbortController().signal });
    expect(run).toEqual({ counts: { ok: 2, failed: 1, untested: 1 }, aborted: false });
    expect(check).toHaveBeenCalledTimes(4);
  });

  it('counts a per-service failure and goes on', async () => {
    const check = vi.fn((s: Service) => (s.id === 2 ? Promise.reject(httpError(404)) : Promise.resolve({ status: 'ok' })));
    const run = await runBulkChecks(TARGETS, check, { signal: new AbortController().signal });
    expect(run.counts).toEqual({ ok: 3, failed: 1, untested: 0 });
    expect(run.stoppedBy).toBeUndefined();
    expect(check).toHaveBeenCalledTimes(4);
  });

  it('stops on a 401, without counting the rest as down', async () => {
    const expired = httpError(401);
    const check = vi.fn((s: Service) => (s.id === 2 ? Promise.reject(expired) : Promise.resolve({ status: 'ok' })));
    const onEnd = vi.fn();
    const run = await runBulkChecks(TARGETS, check, { signal: new AbortController().signal, onEnd });
    expect(run).toEqual({ counts: { ok: 1, failed: 0, untested: 0 }, aborted: false, stoppedBy: expired });
    expect(check).toHaveBeenCalledTimes(2);
    // The service whose check failed is still released from its spinner.
    expect(onEnd).toHaveBeenCalledTimes(2);
  });

  it('stops on a network error', async () => {
    const offline = networkError();
    const check = vi.fn(() => Promise.reject(offline));
    const run = await runBulkChecks(TARGETS, check, { signal: new AbortController().signal });
    expect(run.stoppedBy).toBe(offline);
    expect(check).toHaveBeenCalledTimes(1);
  });

  it('stops as soon as the signal is aborted, and reports nothing', async () => {
    const controller = new AbortController();
    const onResult = vi.fn();
    const check = vi.fn((s: Service) => {
      if (s.id === 2) controller.abort();
      return Promise.resolve({ status: 'ok' });
    });
    const run = await runBulkChecks(TARGETS, check, { signal: controller.signal, onResult });
    expect(run.aborted).toBe(true);
    expect(check).toHaveBeenCalledTimes(2);
    expect(onResult).toHaveBeenCalledTimes(1);
  });

  it('treats a cancelled request as an abort, not a failure', async () => {
    const check = vi.fn(() => Promise.reject(new DOMException('The operation was aborted.', 'AbortError')));
    const run = await runBulkChecks(TARGETS, check, { signal: new AbortController().signal });
    expect(run).toEqual({ counts: { ok: 0, failed: 0, untested: 0 }, aborted: true });
    expect(check).toHaveBeenCalledTimes(1);
  });

  it('sends nothing when aborted before it starts', async () => {
    const controller = new AbortController();
    controller.abort();
    const check = vi.fn(() => Promise.resolve({ status: 'ok' }));
    const run = await runBulkChecks(TARGETS, check, { signal: controller.signal });
    expect(run.aborted).toBe(true);
    expect(check).not.toHaveBeenCalled();
  });
});
