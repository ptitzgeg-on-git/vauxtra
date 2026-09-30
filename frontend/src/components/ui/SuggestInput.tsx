import { forwardRef, useCallback, useEffect, useId, useImperativeHandle, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { Check } from 'lucide-react';
import { Input, type InputProps } from './Input';
import { LIST_SURFACE, listOptionClass, useAnchoredList, useEscapeFirst } from './popover';

export interface SuggestInputProps extends Omit<InputProps, 'list' | 'value' | 'onChange' | 'role'> {
  value: string;
  /** Every change, typed or picked. */
  onValueChange: (value: string) => void;
  /** Values offered under the field. Anything else can still be typed. */
  suggestions: string[];
}

/**
 * A text field that offers known values under it and still accepts any other.
 *
 * It replaces `<input list>` + `<datalist>`, whose suggestion list the browser draws by
 * itself: a plain grey box that ignores the theme, differs from one browser to the next and
 * cannot show which value is already chosen. This one opens the same list as `Select`.
 *
 * Nothing is highlighted until the arrows are used, so Enter on a typed value is never
 * silently swapped for a suggestion.
 */
export const SuggestInput = forwardRef<HTMLInputElement, SuggestInputProps>(function SuggestInput(
  { value, onValueChange, suggestions, onKeyDown, onFocus, onBlur, disabled, ...rest },
  ref,
) {
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  useImperativeHandle(ref, () => inputRef.current as HTMLInputElement);

  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);

  const matches = useMemo(() => {
    const needle = value.trim().toLowerCase();
    // An exact value lists everything again: the field then reads as "change it", not "done".
    if (!needle || suggestions.some((s) => s.toLowerCase() === needle)) return suggestions;
    return suggestions.filter((s) => s.toLowerCase().includes(needle));
  }, [suggestions, value]);

  const close = useCallback(() => {
    setOpen(false);
    setActive(-1);
  }, []);
  const { style, place } = useAnchoredList(inputRef, listRef, open, close);
  useEscapeFirst(open, close);

  const show = () => {
    if (disabled || suggestions.length === 0) return;
    place();
    setOpen(true);
  };

  const pick = (suggestion: string) => {
    onValueChange(suggestion);
    close();
  };

  const visible = open && matches.length > 0;
  const optionId = (index: number) => `${listId}-opt-${index}`;

  useEffect(() => {
    if (!visible || active < 0) return;
    const el = document.getElementById(`${listId}-opt-${active}`);
    if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest' });
  }, [visible, active, listId]);

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    onKeyDown?.(e);
    if (e.defaultPrevented) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!open) show();
      setActive((i) => Math.min(matches.length - 1, i + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => Math.max(-1, i - 1));
    } else if (e.key === 'Enter') {
      if (visible && active >= 0 && matches[active] !== undefined) {
        e.preventDefault();
        pick(matches[active]);
      }
    } else if (e.key === 'Tab') {
      close();
    }
  };

  return (
    <>
      <Input
        ref={inputRef}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={visible}
        aria-controls={listId}
        aria-activedescendant={visible && active >= 0 ? optionId(active) : undefined}
        autoComplete="off"
        disabled={disabled}
        value={value}
        onChange={(e) => {
          onValueChange(e.target.value);
          setActive(-1);
          if (!open) show();
        }}
        onFocus={(e) => {
          onFocus?.(e);
          show();
        }}
        onBlur={(e) => {
          onBlur?.(e);
          close();
        }}
        onKeyDown={handleKeyDown}
        {...rest}
      />
      {visible && style ? (
        createPortal(
          <ul
            ref={listRef}
            id={listId}
            role="listbox"
            tabIndex={-1}
            style={style}
            onMouseDown={(e) => e.preventDefault()}
            className={LIST_SURFACE}
          >
            {matches.map((suggestion, index) => {
              const chosen = suggestion.toLowerCase() === value.trim().toLowerCase();
              return (
                // eslint-disable-next-line jsx-a11y/click-events-have-key-events -- a listbox option; the keys live on the input, which names this one via aria-activedescendant
                <li
                  key={suggestion}
                  id={optionId(index)}
                  role="option"
                  aria-selected={chosen}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => pick(suggestion)}
                  className={listOptionClass(index === active)}
                >
                  <span className="min-w-0 truncate font-mono text-[13px]">{suggestion}</span>
                  {chosen && <Check aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />}
                </li>
              );
            })}
          </ul>,
          document.body,
        )
      ) : (
        <ul id={listId} role="listbox" hidden>
          {suggestions.map((suggestion, index) => (
            <li key={suggestion} id={optionId(index)} role="option" aria-selected={false}>
              {suggestion}
            </li>
          ))}
        </ul>
      )}
    </>
  );
});
