import { useState } from 'react';
import { LayoutTemplate } from 'lucide-react';
import { cn } from '@/components/ui';

export interface TemplateIconProps {
  /** `icon_url` as stored — an absolute URL, a site-relative path, or empty. */
  url?: string | null;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
  /** Told when the image fails, so a form can show "that URL did not load". */
  onLoadError?: () => void;
}

const BOX: Record<NonNullable<TemplateIconProps['size']>, string> = {
  sm: 'h-8 w-8 rounded-lg',
  md: 'h-11 w-11 rounded-xl',
  lg: 'h-14 w-14 rounded-2xl',
};

const GLYPH: Record<NonNullable<TemplateIconProps['size']>, string> = {
  sm: 'h-4 w-4',
  md: 'h-5 w-5',
  lg: 'h-6 w-6',
};

/**
 * The square in front of a template's name: its `icon_url` when the image loads, the
 * template glyph when the field is empty *or* the URL is dead. The `alt` is empty on
 * purpose — the name always sits right next to it, so the image is decorative and a screen
 * reader that read it would say everything twice.
 */
export function TemplateIcon({ url, size = 'md', className, onLoadError }: TemplateIconProps) {
  const src = (url ?? '').trim();
  // The URL that failed, not a plain flag: a corrected URL is therefore retried on the spot,
  // where a boolean would have kept showing the fallback after one typo.
  const [brokenSrc, setBrokenSrc] = useState<string | null>(null);
  const broken = src !== '' && brokenSrc === src;

  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center justify-center overflow-hidden border border-border bg-muted text-primary',
        BOX[size],
        className,
      )}
    >
      {src && !broken ? (
        <img
          src={src}
          alt=""
          loading="lazy"
          className="h-full w-full object-contain p-1"
          onError={() => {
            setBrokenSrc(src);
            onLoadError?.();
          }}
        />
      ) : (
        <LayoutTemplate aria-hidden="true" className={GLYPH[size]} />
      )}
    </span>
  );
}
