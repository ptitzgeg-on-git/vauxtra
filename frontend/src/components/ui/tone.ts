/** The six semantic tones every status-bearing primitive understands. */
export type Tone = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'primary';

export interface ToneClasses {
  /** Foreground colour for text and icons. */
  text: string;
  /** Soft tinted background (10 %). */
  bg: string;
  /** Matching border at 25 %. */
  border: string;
  /** Solid dot / indicator background. */
  dot: string;
  /** Solid fill with its readable foreground, for filled badges and progress bars. */
  solid: string;
}

const TONES: Record<Tone, ToneClasses> = {
  neutral: {
    text: 'text-muted-foreground',
    bg: 'bg-muted',
    border: 'border-border',
    dot: 'bg-muted-foreground',
    solid: 'bg-muted-foreground text-background',
  },
  success: {
    text: 'text-success',
    bg: 'bg-success/10',
    border: 'border-success/25',
    dot: 'bg-success',
    solid: 'bg-success text-success-foreground',
  },
  warning: {
    text: 'text-warning',
    bg: 'bg-warning/10',
    border: 'border-warning/25',
    dot: 'bg-warning',
    solid: 'bg-warning text-warning-foreground',
  },
  danger: {
    text: 'text-destructive',
    bg: 'bg-destructive/10',
    border: 'border-destructive/25',
    dot: 'bg-destructive',
    solid: 'bg-destructive text-destructive-foreground',
  },
  info: {
    text: 'text-info',
    bg: 'bg-info/10',
    border: 'border-info/25',
    dot: 'bg-info',
    solid: 'bg-info text-info-foreground',
  },
  primary: {
    text: 'text-primary',
    bg: 'bg-primary/10',
    border: 'border-primary/25',
    dot: 'bg-primary',
    solid: 'bg-primary text-primary-foreground',
  },
};

/** Token-only class names for a tone, so pages colour a status without touching raw palette classes. */
export function toneClasses(tone: Tone = 'neutral'): ToneClasses {
  return TONES[tone] ?? TONES.neutral;
}
