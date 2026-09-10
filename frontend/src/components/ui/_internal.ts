import { useEffect } from 'react';

/** Shared class lists for text-like form controls (Input, Select, Textarea). */
export const CONTROL_BASE =
  'flex w-full rounded-xl border border-border bg-input text-foreground shadow-sm transition-[border-color,box-shadow] duration-150 ' +
  'placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-60 read-only:bg-muted/50';

export const CONTROL_SIZES = {
  sm: 'h-8 px-2.5 text-xs',
  md: 'h-9 px-3 text-sm',
  lg: 'h-11 px-4 text-base',
} as const;

export type ControlSize = keyof typeof CONTROL_SIZES;

/** Applied when the control is invalid: red border and a red focus ring instead of the primary one. */
export const CONTROL_INVALID = 'border-destructive focus:border-destructive focus:ring-destructive/25 focus-visible:ring-destructive/25';

let lockCount = 0;
let previousOverflow = '';

/** Locks body scrolling while `active`; nests safely (two open dialogs = one lock). */
export function useScrollLock(active: boolean) {
  useEffect(() => {
    if (!active || typeof document === 'undefined') return;
    if (lockCount === 0) {
      previousOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
    }
    lockCount += 1;
    return () => {
      lockCount -= 1;
      if (lockCount === 0) {
        document.body.style.overflow = previousOverflow;
      }
    };
  }, [active]);
}

/** Turns any string into something safe to use inside a DOM id. */
export function slugId(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]+/g, '-');
}

/** True when the platform is a Mac, so shortcut hints show ⌘ instead of Ctrl. */
export function isMacPlatform(): boolean {
  if (typeof navigator === 'undefined') return false;
  return /mac|iphone|ipad|ipod/i.test(navigator.platform || navigator.userAgent || '');
}
