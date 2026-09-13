/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useCallback,
  useContext,
  useId,
  useState,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { cn } from '@/lib/cn';
import { Badge } from './Badge';
import { slugId } from './_internal';

export type TabsVariant = 'underline' | 'pill' | 'segmented';

interface TabsContextValue {
  value: string;
  select: (value: string) => void;
  baseId: string;
  variant: TabsVariant;
}

const TabsContext = createContext<TabsContextValue | null>(null);

function useTabs(component: string): TabsContextValue {
  const ctx = useContext(TabsContext);
  if (!ctx) throw new Error(`${component} must be used inside <Tabs>`);
  return ctx;
}

export const tabId = (baseId: string, value: string) => `${baseId}-tab-${slugId(value)}`;
export const tabPanelId = (baseId: string, value: string) => `${baseId}-panel-${slugId(value)}`;

export interface TabsProps extends Omit<HTMLAttributes<HTMLDivElement>, 'onChange' | 'defaultValue'> {
  /** Controlled value. */
  value?: string;
  /** Initial value when uncontrolled. */
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  variant?: TabsVariant;
  children: ReactNode;
}

/** Tabs root; controlled (`value` + `onValueChange`) or uncontrolled (`defaultValue`). */
export function Tabs({ value, defaultValue, onValueChange, variant = 'underline', className, children, ...rest }: TabsProps) {
  const baseId = useId();
  const [inner, setInner] = useState(defaultValue ?? '');
  const current = value ?? inner;
  const select = useCallback(
    (next: string) => {
      if (value === undefined) setInner(next);
      onValueChange?.(next);
    },
    [value, onValueChange],
  );
  return (
    <TabsContext.Provider value={{ value: current, select, baseId, variant }}>
      <div className={cn('flex flex-col gap-4', className)} {...rest}>
        {children}
      </div>
    </TabsContext.Provider>
  );
}

export interface TabListProps extends HTMLAttributes<HTMLDivElement> {
  /** Accessible name of the tab set. */
  'aria-label': string;
}

const LIST_VARIANTS: Record<TabsVariant, string> = {
  underline: 'flex items-end gap-1 border-b border-border overflow-x-auto scrollbar-none',
  pill: 'flex flex-wrap items-center gap-1',
  segmented: 'inline-flex items-center gap-1 rounded-xl bg-muted p-1',
};

/** The row of tabs; handles ArrowLeft/Right, Home and End with a roving tabindex. */
export function TabList({ className, onKeyDown, ...rest }: TabListProps) {
  const { variant } = useTabs('TabList');

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    onKeyDown?.(e);
    if (e.defaultPrevented) return;
    const tabs = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]:not([disabled])'));
    if (tabs.length === 0) return;
    const currentIndex = tabs.findIndex((el) => el === document.activeElement);
    let next = -1;
    switch (e.key) {
      case 'ArrowRight':
        next = currentIndex < 0 ? 0 : (currentIndex + 1) % tabs.length;
        break;
      case 'ArrowLeft':
        next = currentIndex < 0 ? tabs.length - 1 : (currentIndex - 1 + tabs.length) % tabs.length;
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = tabs.length - 1;
        break;
      default:
        return;
    }
    e.preventDefault();
    tabs[next].focus();
    tabs[next].click();
  };

  // eslint-disable-next-line jsx-a11y/interactive-supports-focus -- roving focus lives on the tabs; the tablist itself is never a stop
  return <div role="tablist" className={cn(LIST_VARIANTS[variant], className)} onKeyDown={handleKeyDown} {...rest} />;
}

export interface TabProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'value' | 'type'> {
  value: string;
  icon?: ReactNode;
  /** Small count badge after the label. */
  count?: number | string;
}

const TAB_BASE =
  'inline-flex items-center gap-2 whitespace-nowrap text-sm font-medium transition-colors duration-150 ' +
  'disabled:opacity-50 disabled:pointer-events-none focus-visible:ring-offset-0';

const TAB_VARIANTS: Record<TabsVariant, { base: string; active: string; inactive: string }> = {
  underline: {
    base: 'px-3 py-2.5 -mb-px border-b-2 rounded-t-md',
    active: 'border-primary text-foreground',
    inactive: 'border-transparent text-muted-foreground hover:text-foreground hover:border-border',
  },
  pill: {
    base: 'h-8 rounded-lg px-3',
    active: 'bg-primary/10 text-primary',
    inactive: 'text-muted-foreground hover:bg-accent hover:text-foreground',
  },
  segmented: {
    base: 'h-8 rounded-lg px-3',
    active: 'bg-card text-foreground shadow-sm',
    inactive: 'text-muted-foreground hover:text-foreground',
  },
};

/** One tab button; `value` links it to the `TabPanel` with the same value. */
export function Tab({ value, icon, count, className, children, onClick, ...rest }: TabProps) {
  const { value: current, select, baseId, variant } = useTabs('Tab');
  const selected = current === value;
  const v = TAB_VARIANTS[variant];
  return (
    <button
      type="button"
      role="tab"
      id={tabId(baseId, value)}
      aria-selected={selected}
      aria-controls={tabPanelId(baseId, value)}
      tabIndex={selected ? 0 : -1}
      onClick={(e) => {
        onClick?.(e);
        if (!e.defaultPrevented) select(value);
      }}
      className={cn(TAB_BASE, v.base, selected ? v.active : v.inactive, className)}
      {...rest}
    >
      {icon && <span aria-hidden="true" className="inline-flex shrink-0 [&>svg]:h-4 [&>svg]:w-4">{icon}</span>}
      {children}
      {count !== undefined && (
        <Badge size="sm" tone={selected ? 'primary' : 'neutral'}>
          {count}
        </Badge>
      )}
    </button>
  );
}

export interface TabPanelProps extends HTMLAttributes<HTMLDivElement> {
  value: string;
  /** Keep the panel in the DOM (hidden) when not selected, preserving its state. */
  keepMounted?: boolean;
}

/** The content for one tab; rendered only while selected unless `keepMounted`. */
export function TabPanel({ value, keepMounted = false, className, children, ...rest }: TabPanelProps) {
  const { value: current, baseId } = useTabs('TabPanel');
  const selected = current === value;
  if (!selected && !keepMounted) return null;
  return (
    <div
      role="tabpanel"
      id={tabPanelId(baseId, value)}
      aria-labelledby={tabId(baseId, value)}
      hidden={!selected}
      tabIndex={0}
      className={cn('focus-visible:ring-offset-0 animate-in fade-in', className)}
      {...rest}
    >
      {children}
    </div>
  );
}
