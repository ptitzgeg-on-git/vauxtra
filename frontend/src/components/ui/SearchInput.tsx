import { forwardRef, type InputHTMLAttributes } from 'react';
import { Search, X } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useT } from '@/i18n';
import { CONTROL_BASE, CONTROL_SIZES, type ControlSize } from './_internal';

export interface SearchInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'onChange' | 'value' | 'size' | 'type'> {
  value: string;
  onChange: (value: string) => void;
  size?: ControlSize;
  wrapperClassName?: string;
}

/** Search field with a leading icon and a clear button; controlled through `value` / `onChange(string)`. */
export const SearchInput = forwardRef<HTMLInputElement, SearchInputProps>(function SearchInput(
  { value, onChange, size = 'md', className, wrapperClassName, placeholder, ...rest },
  ref,
) {
  const t = useT();
  // A placeholder is not an accessible name: it disappears as soon as the field has text, and
  // several screen readers never use it for the name at all. The same string is therefore also
  // the default `aria-label`, so a call site that passes only a placeholder is still named --
  // `...rest` is spread last, so an explicit `aria-label` (or `aria-labelledby`) still wins.
  const fallbackLabel = placeholder ?? t('ui.search.placeholder');
  return (
    <div className={cn('relative', wrapperClassName)}>
      <Search
        aria-hidden="true"
        className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
      />
      <input
        ref={ref}
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={fallbackLabel}
        aria-label={fallbackLabel}
        className={cn(
          CONTROL_BASE,
          CONTROL_SIZES[size],
          'pl-9 pr-9 [&::-webkit-search-cancel-button]:hidden [&::-webkit-search-decoration]:hidden',
          className,
        )}
        {...rest}
      />
      {value.length > 0 && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label={t('ui.search.clear')}
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
        >
          <X aria-hidden="true" className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
});
