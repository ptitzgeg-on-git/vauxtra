import { forwardRef, type HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** Lifts on hover and shows a pointer — for cards that are themselves a target. */
  interactive?: boolean;
  /** Uses the elevated surface and shadow (menus, popovers, highlighted panels). */
  elevated?: boolean;
}

/** The surface every panel sits on: rounded-2xl, token border, card background, card shadow. */
export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { className, interactive = false, elevated = false, ...rest },
  ref,
) {
  return (
    <div
      ref={ref}
      className={cn(
        'rounded-2xl border border-border bg-card text-foreground shadow-card',
        elevated && 'bg-card-elevated shadow-elevated',
        interactive &&
          'cursor-pointer transition-[transform,box-shadow,border-color] duration-200 ease-out-expo hover:-translate-y-0.5 hover:shadow-elevated hover:border-primary/30',
        className,
      )}
      {...rest}
    />
  );
});

/** Top slot of a card: title, description and optional right-hand actions in a row. */
export const CardHeader = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(function CardHeader(
  { className, ...rest },
  ref,
) {
  return <div ref={ref} className={cn('flex flex-col gap-1 p-5 sm:p-6', className)} {...rest} />;
});

/** Card title (an `h3` by default). */
export const CardTitle = forwardRef<HTMLHeadingElement, HTMLAttributes<HTMLHeadingElement>>(function CardTitle(
  { className, ...rest },
  ref,
) {
  // eslint-disable-next-line jsx-a11y/heading-has-content -- a heading primitive; its content arrives as children at the call site
  return <h3 ref={ref} className={cn('text-base font-semibold leading-tight tracking-tight text-foreground', className)} {...rest} />;
});

/** Muted one-liner under the card title. */
export const CardDescription = forwardRef<HTMLParagraphElement, HTMLAttributes<HTMLParagraphElement>>(
  function CardDescription({ className, ...rest }, ref) {
    return <p ref={ref} className={cn('text-sm text-muted-foreground', className)} {...rest} />;
  },
);

/** Card body; sits flush under a `CardHeader`. */
export const CardContent = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(function CardContent(
  { className, ...rest },
  ref,
) {
  return <div ref={ref} className={cn('p-5 pt-0 sm:p-6 sm:pt-0', className)} {...rest} />;
});

/** Card footer row, usually right-aligned actions. */
export const CardFooter = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(function CardFooter(
  { className, ...rest },
  ref,
) {
  return (
    <div
      ref={ref}
      className={cn('flex items-center gap-3 border-t border-border bg-muted/40 px-5 py-4 sm:px-6 rounded-b-2xl', className)}
      {...rest}
    />
  );
});
