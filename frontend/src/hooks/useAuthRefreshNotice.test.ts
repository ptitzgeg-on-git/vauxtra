/**
 * A failed refresh of `/auth/me` keeps the last answer on screen, and has to say so: it used
 * to keep it without a word, so a session that had ended on the server still looked open.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import toast from 'react-hot-toast';
import { AUTH_REFRESH_TOAST_ID, useAuthRefreshNotice } from './useAuthRefreshNotice';

describe('useAuthRefreshNotice', () => {
  beforeEach(() => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
  });
  afterEach(() => toast.remove());

  it('raises one toast and logs when a refresh fails over a known answer', () => {
    const error = vi.spyOn(toast, 'error');

    renderHook(() => useAuthRefreshNotice(true, new Error('Network Error'), 1));

    expect(error).toHaveBeenCalledWith('login.session_check_failed', { id: AUTH_REFRESH_TOAST_ID });
    expect(console.warn).toHaveBeenCalledOnce();
  });

  it('stays quiet on the first load, which has its own error screen', () => {
    const error = vi.spyOn(toast, 'error');

    renderHook(() => useAuthRefreshNotice(false, new Error('Network Error'), 1));

    expect(error).not.toHaveBeenCalled();
  });

  it('reports each new failure and takes the warning down once a refresh answers', () => {
    const error = vi.spyOn(toast, 'error');
    const dismiss = vi.spyOn(toast, 'dismiss');
    const failure = new Error('Network Error');

    const { rerender } = renderHook(
      ({ err, at }: { err: unknown; at: number }) => useAuthRefreshNotice(true, err, at),
      { initialProps: { err: failure as unknown, at: 1 } },
    );
    rerender({ err: failure, at: 2 });
    expect(error).toHaveBeenCalledTimes(2);

    rerender({ err: null, at: 2 });
    expect(dismiss).toHaveBeenCalledWith(AUTH_REFRESH_TOAST_ID);
  });
});
