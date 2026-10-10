/**
 * The wizard reloads its integrations on mount when it reopens past the welcome screen. That
 * answer could land after the wizard was gone (setup finished, page left) and still write
 * state and raise a toast over whatever screen came next.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { act } from '@testing-library/react';
import toast from 'react-hot-toast';
import { api } from '@/api/client';
import { renderWithProviders } from '@/test/render';
import { ThemeProvider } from '@/theme';
import { Setup } from './Setup';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('Setup mount load', () => {
  afterEach(() => sessionStorage.clear());

  it('drops the integrations answer that arrives after the wizard unmounted', async () => {
    sessionStorage.setItem('vauxtra.setup.step', JSON.stringify('providers'));
    const providers = deferred<unknown>();
    vi.spyOn(api, 'get').mockImplementation(((url: string) =>
      url === '/providers' ? providers.promise : new Promise(() => {})) as never);
    const error = vi.spyOn(toast, 'error');

    const { unmount } = renderWithProviders(
      <ThemeProvider>
        <Setup onComplete={() => {}} />
      </ThemeProvider>,
    );
    expect(api.get).toHaveBeenCalledWith('/providers');

    unmount();
    await act(async () => {
      providers.reject(new Error('Network Error'));
      await providers.promise.catch(() => {});
    });

    expect(error).not.toHaveBeenCalled();
  });
});
