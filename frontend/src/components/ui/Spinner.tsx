import { LoaderCircle } from 'lucide-react';
import { cn } from '@/lib/cn';

export type SpinnerSize = 'xs' | 'sm' | 'md' | 'lg';

export interface SpinnerProps {
  size?: SpinnerSize;
  className?: string;
  /** Visually hidden text for screen readers; without it the spinner is decorative. */
  label?: string;
}

const SIZES: Record<SpinnerSize, string> = {
  xs: 'h-3 w-3',
  sm: 'h-4 w-4',
  md: 'h-5 w-5',
  lg: 'h-8 w-8',
};

/** A spinning loader icon; pass `label` when it is the only thing announcing a wait. */
export function Spinner({ size = 'md', className, label }: SpinnerProps) {
  return (
    <span role={label ? 'status' : undefined} className={cn('inline-flex items-center justify-center', className)}>
      <LoaderCircle aria-hidden="true" className={cn('animate-spin', SIZES[size])} />
      {label && <span className="sr-only">{label}</span>}
    </span>
  );
}
