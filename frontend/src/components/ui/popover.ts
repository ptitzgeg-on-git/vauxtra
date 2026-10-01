import { useCallback, useEffect, useState, type CSSProperties, type RefObject } from 'react';

const GAP = 4;
const MARGIN = 8;

/**
 * Where a list anchored to `anchor` goes: under it, or over it when the room below is too
 * short and the room above is larger. Fixed-positioned and portalled by the caller, so no
 * ancestor's `overflow` can clip it -- a list opened near the bottom of a scrolling modal
 * body stays whole instead of being cut at the body's edge.
 */
export function anchoredStyle(anchor: HTMLElement, maxHeight = 288): CSSProperties {
  const r = anchor.getBoundingClientRect();
  const vh = window.innerHeight;
  const vw = window.innerWidth;
  const below = vh - r.bottom - GAP - MARGIN;
  const above = r.top - GAP - MARGIN;
  const up = below < Math.min(maxHeight, 180) && above > below;
  const room = Math.max(120, Math.min(maxHeight, up ? above : below));
  const width = Math.max(r.width, 160);
  const left = Math.max(MARGIN, Math.min(r.left, vw - width - MARGIN));
  return up
    ? { position: 'fixed', left, width, bottom: vh - r.top + GAP, maxHeight: room }
    : { position: 'fixed', left, width, top: r.bottom + GAP, maxHeight: room };
}

/**
 * The position of a list attached to `anchorRef`, for as long as it is open.
 *
 * `place()` measures; the opener calls it in the same handler that opens the list, so the
 * first frame is already in the right spot (a state update in the effect body would paint
 * one frame at 0,0 first). While open, a resize re-measures, and a scroll anywhere but in
 * the list itself closes it through `onDismiss`: a list left floating over a page that moved
 * underneath it points at nothing.
 */
export function useAnchoredList(
  anchorRef: RefObject<HTMLElement | null>,
  listRef: RefObject<HTMLElement | null>,
  open: boolean,
  onDismiss: () => void,
  maxHeight?: number,
) {
  const [style, setStyle] = useState<CSSProperties | null>(null);

  const place = useCallback(() => {
    const anchor = anchorRef.current;
    if (anchor) setStyle(anchoredStyle(anchor, maxHeight));
  }, [anchorRef, maxHeight]);

  useEffect(() => {
    if (!open) return;
    const onScroll = (e: Event) => {
      const list = listRef.current;
      if (list && e.target instanceof Node && list.contains(e.target)) return;
      onDismiss();
    };
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node | null;
      if (!target) return;
      if (anchorRef.current?.contains(target) || listRef.current?.contains(target)) return;
      onDismiss();
    };
    window.addEventListener('resize', place);
    window.addEventListener('scroll', onScroll, true);
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', onScroll, true);
      document.removeEventListener('pointerdown', onPointerDown, true);
    };
  }, [open, place, onDismiss, anchorRef, listRef]);

  return { style: open ? style : null, place };
}

/**
 * Escape closes the open list, and only the list.
 *
 * Every dialog listens for Escape on `document` in the capture phase (`useModalDialog`), so a
 * handler on the control itself runs too late: the dialog around the select would close with
 * it. Window is earlier on the capture path than document, so a listener there, stopped at
 * once, is the one key press that never reaches the dialog.
 */
export function useEscapeFirst(open: boolean, onEscape: () => void) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopPropagation();
      onEscape();
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [open, onEscape]);
}

/** The list surface both the select and the suggestion field draw: same as the time-zone picker's. */
export const LIST_SURFACE =
  'z-[80] overflow-y-auto rounded-xl border border-border bg-popover p-1 text-popover-foreground shadow-elevated animate-in fade-in zoom-in-95 animate-duration-150';

/** One row of such a list; `active` is the keyboard/pointer highlight. */
export function listOptionClass(active: boolean, disabled = false): string {
  return [
    'flex items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm select-none',
    disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
    active && !disabled ? 'bg-accent text-foreground' : 'text-foreground/90',
  ].join(' ');
}
