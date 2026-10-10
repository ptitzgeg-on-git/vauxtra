import { useEffect } from 'react';
import toast from 'react-hot-toast';
import { useT } from '@/i18n';

/** One toast at a time, however many refreshes fail in a row. */
export const AUTH_REFRESH_TOAST_ID = 'auth-refresh-failed';

/**
 * Say so when a refresh of `/auth/me` fails while an earlier answer is still on screen.
 *
 * The gate keeps that earlier answer on purpose (one dropped request should not replace the
 * whole app with an error page), but it used to keep it silently: the session could have
 * ended on the server and nothing told the operator that the screen was no longer confirmed.
 *
 * `errorUpdatedAt` is the trigger rather than `error` so that two failures carrying equal
 * errors are still two failures.
 */
export function useAuthRefreshNotice(hasAnswer: boolean, error: unknown, errorUpdatedAt: number): void {
  const t = useT();
  useEffect(() => {
    if (!hasAnswer) return;
    if (!error) {
      // A later refresh answered: the warning no longer holds.
      toast.dismiss(AUTH_REFRESH_TOAST_ID);
      return;
    }
    console.warn('Vauxtra: refreshing /auth/me failed; keeping the last known session state.', error);
    toast.error(t('login.session_check_failed'), { id: AUTH_REFRESH_TOAST_ID });
    // `t` changes with the language; a language switch is not a new failure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasAnswer, error, errorUpdatedAt]);
}
