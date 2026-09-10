import {
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/cn';

export type TooltipPlacement = 'top' | 'bottom' | 'left' | 'right';

export interface TooltipProps {
  content: ReactNode;
  placement?: TooltipPlacement;
  /** Hover delay in ms; keyboard focus shows it immediately. */
  delay?: number;
  children: ReactNode;
  /** Class for the inline wrapper around the trigger (e.g. `w-full`). */
  className?: string;
  /** Class for the bubble. */
  contentClassName?: string;
  disabled?: boolean;
}

const GAP = 8;
const EDGE = 8;

type DescribedBy = { 'aria-describedby'?: string };

/**
 * Hover + focus tooltip on the popover tokens. Rendered in a portal with fixed positioning,
 * so it escapes `overflow: hidden` parents (the collapsed sidebar, table cells, scroll areas).
 */
export function Tooltip({
  content,
  placement = 'top',
  delay = 150,
  children,
  className,
  contentClassName,
  disabled = false,
}: TooltipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLSpanElement>(null);
  const bubbleRef = useRef<HTMLDivElement>(null);
  const timer = useRef<number | null>(null);

  const clear = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const show = useCallback(
    (immediate: boolean) => {
      if (disabled) return;
      clear();
      if (immediate || delay <= 0) {
        setOpen(true);
        return;
      }
      timer.current = window.setTimeout(() => setOpen(true), delay);
    },
    [clear, delay, disabled],
  );

  const hide = useCallback(() => {
    clear();
    setOpen(false);
  }, [clear]);

  useEffect(() => clear, [clear]);

  // Close on Escape and on any scroll or resize (positions go stale) while open.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    const onMove = () => setOpen(false);
    document.addEventListener('keydown', onKey);
    window.addEventListener('scroll', onMove, true);
    window.addEventListener('resize', onMove);
    return () => {
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onMove, true);
      window.removeEventListener('resize', onMove);
    };
  }, [open]);

  // The position is written straight to the bubble's style: it is measured after paint and
  // never needs a second render.
  useLayoutEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current;
    const bubble = bubbleRef.current;
    if (!trigger || !bubble) return;
    const r = trigger.getBoundingClientRect();
    const b = bubble.getBoundingClientRect();
    let top = 0;
    let left = 0;
    switch (placement) {
      case 'bottom':
        top = r.bottom + GAP;
        left = r.left + r.width / 2 - b.width / 2;
        break;
      case 'left':
        top = r.top + r.height / 2 - b.height / 2;
        left = r.left - b.width - GAP;
        break;
      case 'right':
        top = r.top + r.height / 2 - b.height / 2;
        left = r.right + GAP;
        break;
      case 'top':
      default:
        top = r.top - b.height - GAP;
        left = r.left + r.width / 2 - b.width / 2;
    }
    left = Math.max(EDGE, Math.min(left, window.innerWidth - b.width - EDGE));
    top = Math.max(EDGE, Math.min(top, window.innerHeight - b.height - EDGE));
    bubble.style.top = `${Math.round(top)}px`;
    bubble.style.left = `${Math.round(left)}px`;
    bubble.style.visibility = 'visible';
  }, [open, placement, content]);

  const child = isValidElement<DescribedBy>(children)
    ? cloneElement(children, { 'aria-describedby': open ? id : children.props['aria-describedby'] })
    : children;

  return (
    <span
      ref={triggerRef}
      className={cn('inline-flex', className)}
      onMouseEnter={() => show(false)}
      onMouseLeave={hide}
      onFocus={() => show(true)}
      onBlur={hide}
    >
      {child}
      {open &&
        !disabled &&
        typeof document !== 'undefined' &&
        createPortal(
          <div
            ref={bubbleRef}
            id={id}
            role="tooltip"
            style={{ top: 0, left: 0, visibility: 'hidden' }}
            className={cn(
              'pointer-events-none fixed z-[70] max-w-xs rounded-lg border border-border bg-popover px-2.5 py-1.5',
              'text-xs font-medium leading-snug text-popover-foreground shadow-elevated',
              'animate-in fade-in zoom-in-95 animate-duration-150',
              contentClassName,
            )}
          >
            {content}
          </div>,
          document.body,
        )}
    </span>
  );
}
