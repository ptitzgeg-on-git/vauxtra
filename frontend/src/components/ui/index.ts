/**
 * Vauxtra UI primitives — import from `@/components/ui`.
 * Every component is token-only (dark mode comes for free) and merges `className` through `cn`.
 */
export { cn } from '@/lib/cn';
export { toneClasses, type Tone, type ToneClasses } from './tone';

export { Button, buttonVariants, type ButtonProps, type ButtonVariant, type ButtonSize, type ButtonVariantOptions } from './Button';
export { IconButton, type IconButtonProps } from './IconButton';
export { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter, type CardProps } from './Card';
export { Badge, type BadgeProps } from './Badge';
export { PageHeader, type PageHeaderProps } from './PageHeader';
export { SectionHeading, type SectionHeadingProps } from './SectionHeading';
export { EmptyState, type EmptyStateProps } from './EmptyState';
export { Skeleton, SkeletonText, SkeletonCard, SkeletonRow, type SkeletonTextProps, type SkeletonRowProps } from './Skeleton';
export { StatCard, type StatCardProps, type StatTrend } from './StatCard';
export { Tabs, TabList, Tab, TabPanel, type TabsProps, type TabListProps, type TabProps, type TabPanelProps, type TabsVariant } from './Tabs';
export { Modal, type ModalProps, type ModalSize } from './Modal';
export { Drawer, type DrawerProps, type DrawerSize, type DrawerSide } from './Drawer';
export { Field, useFieldControl, type FieldProps, type FieldControlProps } from './Field';
export { Input, type InputProps } from './Input';
export { Select, type SelectProps } from './Select';
export { Textarea, type TextareaProps } from './Textarea';
export { Switch, type SwitchProps } from './Switch';
export { Checkbox, type CheckboxProps } from './Checkbox';
export { Kbd, type KbdProps } from './Kbd';
export { Tooltip, type TooltipProps, type TooltipPlacement } from './Tooltip';
export { InlineAlert, type InlineAlertProps, type InlineAlertTone } from './InlineAlert';
export { Spinner, type SpinnerProps, type SpinnerSize } from './Spinner';
export { Separator, type SeparatorProps } from './Separator';
export { SearchInput, type SearchInputProps } from './SearchInput';
export { Chip, ChipGroup, type ChipProps, type ChipGroupProps } from './Chip';
export { ProgressBar, type ProgressBarProps } from './ProgressBar';
export { FieldHint } from './FieldHint';

// Owned by other files; re-exported unchanged so pages have a single import.
export { ConfirmDialog, useConfirmDialog, type ConfirmDialogProps } from './ConfirmDialog';
export { ErrorBoundary } from './ErrorBoundary';
export { ProviderLogo } from './ProviderLogos';
