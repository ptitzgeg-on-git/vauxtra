import {
  Children,
  Fragment,
  forwardRef,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type FocusEventHandler,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useFieldControl } from './Field';
import { CONTROL_BASE, CONTROL_INVALID, CONTROL_SIZES, type ControlSize } from './_internal';
import { LIST_SURFACE, listOptionClass, useAnchoredList, useEscapeFirst } from './popover';

/** What `onChange` receives: the shape every caller already reads (`e.target.value`). */
export interface SelectChangeEvent {
  target: { value: string; name: string };
  currentTarget: { value: string; name: string };
}

export interface SelectProps {
  id?: string;
  /** Submits the value through a hidden input, for the rare form read by `FormData`. */
  name?: string;
  value?: string | number;
  defaultValue?: string | number;
  onChange?: (event: SelectChangeEvent) => void;
  disabled?: boolean;
  required?: boolean;
  invalid?: boolean;
  size?: ControlSize;
  className?: string;
  wrapperClassName?: string;
  title?: string;
  onBlur?: FocusEventHandler<HTMLButtonElement>;
  onFocus?: FocusEventHandler<HTMLButtonElement>;
  'aria-label'?: string;
  'aria-labelledby'?: string;
  'aria-describedby'?: string;
  'aria-invalid'?: boolean | 'true' | 'false';
  /** `<option>` elements, optionally inside `<optgroup>`s or fragments -- as for a native select. */
  children?: ReactNode;
}

interface ParsedOption {
  value: string;
  label: ReactNode;
  /** Plain text of the label, for type-ahead. */
  text: string;
  disabled: boolean;
  group?: string;
}

function textOf(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join('');
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return '';
}

type OptionLike = { value?: string | number; children?: ReactNode; disabled?: boolean; label?: string };

function collectOptions(children: ReactNode, out: ParsedOption[] = [], group?: string): ParsedOption[] {
  Children.forEach(children, (child) => {
    if (!isValidElement<OptionLike>(child)) return;
    if (child.type === Fragment) {
      collectOptions(child.props.children, out, group);
    } else if (child.type === 'optgroup') {
      collectOptions(child.props.children, out, child.props.label);
    } else if (child.type === 'option') {
      const text = textOf(child.props.children);
      out.push({
        value: child.props.value !== undefined ? String(child.props.value) : text,
        label: child.props.children,
        text,
        disabled: Boolean(child.props.disabled),
        group,
      });
    }
  });
  return out;
}

const TYPEAHEAD_RESET_MS = 600;

/**
 * A select drawn by the app instead of the operating system.
 *
 * The native `<select>` opened the platform's own list -- a grey system menu on Windows, a
 * white one over the dark theme on some Linux builds, blue highlight everywhere -- the one
 * part of every form that looked borrowed. This one opens the same list the time-zone picker
 * already drew, and keeps the native API: `value`, `onChange(e => e.target.value)` and
 * `<option>` children, so no call site changes.
 *
 * Keyboard follows the WAI-ARIA "select-only combobox": focus stays on the trigger, the
 * highlighted option is named through `aria-activedescendant`; arrows, Home/End, Page keys,
 * Enter/Space to pick, Escape to close, type-ahead on the option text.
 */
export const Select = forwardRef<HTMLButtonElement, SelectProps>(function Select(
  {
    id,
    name,
    value,
    defaultValue,
    onChange,
    disabled = false,
    required,
    invalid,
    size = 'md',
    className,
    wrapperClassName,
    title,
    onBlur,
    onFocus,
    'aria-label': ariaLabel,
    'aria-labelledby': ariaLabelledBy,
    'aria-describedby': ariaDescribedBy,
    'aria-invalid': ariaInvalid,
    children,
  },
  ref,
) {
  const control = useFieldControl({ id, invalid, required, 'aria-describedby': ariaDescribedBy, 'aria-invalid': ariaInvalid });
  const listId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  useImperativeHandle(ref, () => triggerRef.current as HTMLButtonElement);

  const options = useMemo(() => collectOptions(children), [children]);
  const [internal, setInternal] = useState(defaultValue !== undefined ? String(defaultValue) : undefined);
  const current = value !== undefined ? String(value) : (internal ?? options[0]?.value ?? '');
  const matched = options.findIndex((o) => o.value === current);
  // A native select shows its first option when the value matches none; the pages rely on it.
  const shownIndex = matched >= 0 ? matched : options.length > 0 ? 0 : -1;
  const shown = shownIndex >= 0 ? options[shownIndex] : undefined;

  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const typeahead = useRef({ text: '', at: 0 });

  const close = useCallback(() => setOpen(false), []);
  const { style, place } = useAnchoredList(triggerRef, listRef, open, close);
  useEscapeFirst(open, close);

  const enabledFrom = (start: number, step: 1 | -1): number => {
    for (let i = start; i >= 0 && i < options.length; i += step) {
      if (!options[i].disabled) return i;
    }
    return -1;
  };
  const firstEnabled = () => enabledFrom(0, 1);
  const lastEnabled = () => enabledFrom(options.length - 1, -1);

  const openAt = (index: number) => {
    if (disabled || options.length === 0) return;
    place();
    setActive(index >= 0 ? index : firstEnabled());
    setOpen(true);
  };

  const commit = (index: number) => {
    const option = options[index];
    if (!option || option.disabled) return;
    setOpen(false);
    if (option.value === current) return;
    if (value === undefined) setInternal(option.value);
    const target = { value: option.value, name: name ?? '' };
    onChange?.({ target, currentTarget: target });
  };

  const move = (from: number, delta: number) => {
    const step: 1 | -1 = delta > 0 ? 1 : -1;
    const target = Math.max(0, Math.min(options.length - 1, from + delta));
    const next = enabledFrom(target, step);
    const fallback = enabledFrom(target, step === 1 ? -1 : 1);
    return next >= 0 ? next : fallback >= 0 ? fallback : from;
  };

  /** Next option whose text starts with what was just typed, from the one after `from`. */
  const matchTyped = (char: string, from: number): number => {
    const now = Date.now();
    const buffer = now - typeahead.current.at > TYPEAHEAD_RESET_MS ? char : typeahead.current.text + char;
    typeahead.current = { text: buffer, at: now };
    const needle = buffer.toLowerCase();
    // A repeated single letter cycles through the options that start with it.
    const cycling = needle.length > 1 && [...needle].every((c) => c === needle[0]);
    const search = cycling ? needle[0] : needle;
    const start = cycling || buffer.length === 1 ? from + 1 : from;
    for (let k = 0; k < options.length; k += 1) {
      const i = (start + k + options.length) % options.length;
      if (!options[i].disabled && options[i].text.toLowerCase().startsWith(search)) return i;
    }
    return -1;
  };

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    const printable = e.key.length === 1 && e.key !== ' ' && !e.ctrlKey && !e.metaKey && !e.altKey;

    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openAt(shownIndex);
      } else if (e.key === 'Home' || e.key === 'End') {
        e.preventDefault();
        openAt(e.key === 'Home' ? firstEnabled() : lastEnabled());
      } else if (printable) {
        e.preventDefault();
        const hit = matchTyped(e.key, shownIndex);
        openAt(hit >= 0 ? hit : shownIndex);
      }
      return;
    }

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setActive((i) => move(i, 1));
        break;
      case 'ArrowUp':
        e.preventDefault();
        if (e.altKey) commit(active);
        else setActive((i) => move(i, -1));
        break;
      case 'Home':
        e.preventDefault();
        setActive(firstEnabled());
        break;
      case 'End':
        e.preventDefault();
        setActive(lastEnabled());
        break;
      case 'PageDown':
        e.preventDefault();
        setActive((i) => move(i, 10));
        break;
      case 'PageUp':
        e.preventDefault();
        setActive((i) => move(i, -10));
        break;
      case 'Enter':
      case ' ':
        e.preventDefault();
        commit(active);
        break;
      case 'Tab':
        setOpen(false);
        break;
      default:
        if (printable) {
          e.preventDefault();
          const hit = matchTyped(e.key, active);
          if (hit >= 0) setActive(hit);
        }
    }
  };

  const optionId = (index: number) => `${listId}-opt-${index}`;

  // Keep the highlighted option in view as the keyboard walks the list.
  useEffect(() => {
    if (!open || active < 0) return;
    const el = document.getElementById(`${listId}-opt-${active}`);
    if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest' });
  }, [open, active, listId]);

  const labelledBy = ariaLabelledBy ?? (ariaLabel ? undefined : control.labelId);

  const renderOptions = (interactive: boolean) =>
    options.map((option, index) => {
      const selected = index === matched;
      const startsGroup = option.group && (index === 0 || options[index - 1].group !== option.group);
      return (
        <Fragment key={`${option.value}-${index}`}>
          {startsGroup && (
            <li role="presentation" className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {option.group}
            </li>
          )}
          {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events -- a listbox option; the keys live on the trigger, which names this one via aria-activedescendant */}
          <li
            id={optionId(index)}
            role="option"
            aria-selected={selected}
            aria-disabled={option.disabled || undefined}
            data-value={option.value}
            onMouseEnter={
              interactive
                ? () => {
                    if (!option.disabled) setActive(index);
                  }
                : undefined
            }
            onClick={interactive ? () => commit(index) : undefined}
            className={listOptionClass(interactive && index === active, option.disabled)}
          >
            <span className="min-w-0 truncate">{option.label}</span>
            {selected && <Check aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />}
          </li>
        </Fragment>
      );
    });

  return (
    <div className={cn('relative', wrapperClassName)}>
      <button
        ref={triggerRef}
        type="button"
        id={control.id}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open && active >= 0 ? optionId(active) : undefined}
        aria-label={ariaLabel}
        aria-labelledby={labelledBy}
        aria-describedby={control.describedBy}
        aria-invalid={control.invalid || undefined}
        aria-required={control.required || undefined}
        data-value={current}
        title={title}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openAt(shownIndex))}
        onKeyDown={onKeyDown}
        onBlur={onBlur}
        onFocus={onFocus}
        className={cn(
          CONTROL_BASE,
          CONTROL_SIZES[size],
          'items-center pr-9 text-left cursor-pointer',
          'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary/25 focus-visible:ring-offset-0 focus-visible:border-primary',
          open && 'border-primary ring-2 ring-primary/25',
          control.invalid && CONTROL_INVALID,
          className,
        )}
      >
        <span className="block min-w-0 truncate">{shown?.label}</span>
      </button>
      <ChevronDown
        aria-hidden="true"
        className={cn(
          'pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground transition-transform duration-150',
          open && 'rotate-180',
        )}
      />
      {name && <input type="hidden" name={name} value={current} />}
      {open && style ? (
        createPortal(
          <ul
            ref={listRef}
            id={listId}
            role="listbox"
            aria-label={ariaLabel}
            aria-labelledby={labelledBy}
            tabIndex={-1}
            style={style}
            // The trigger keeps focus: a press on the list must not blur it.
            onMouseDown={(e) => e.preventDefault()}
            className={LIST_SURFACE}
          >
            {renderOptions(true)}
          </ul>,
          document.body,
        )
      ) : (
        // Closed, the list stays in the page, hidden: `aria-controls` always names a real
        // element, and the options can be read without opening anything.
        <ul id={listId} role="listbox" aria-label={ariaLabel} aria-labelledby={labelledBy} hidden>
          {renderOptions(false)}
        </ul>
      )}
    </div>
  );
});
