import { useCallback, useSyncExternalStore } from 'react';

/**
 * Whether a CSS media query currently matches, kept live as the viewport changes.
 * Answers `false` wherever `matchMedia` is missing (jsdom, very old browsers), which is
 * the desktop layout -- the same fallback `theme.tsx` uses.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {};
      const media = window.matchMedia(query);
      media.addEventListener('change', onChange);
      return () => media.removeEventListener('change', onChange);
    },
    [query],
  );
  const read = () =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches;
  return useSyncExternalStore(subscribe, read, () => false);
}
