/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: 'rgb(var(--vx-bg) / <alpha-value>)',
        foreground: 'rgb(var(--vx-fg) / <alpha-value>)',
        card: 'rgb(var(--vx-card) / <alpha-value>)',
        'card-foreground': 'rgb(var(--vx-fg) / <alpha-value>)',
        'card-elevated': 'rgb(var(--vx-card-elevated) / <alpha-value>)',
        border: 'rgb(var(--vx-border) / <alpha-value>)',
        input: 'rgb(var(--vx-input) / <alpha-value>)',
        ring: 'rgb(var(--vx-ring) / <alpha-value>)',
        muted: 'rgb(var(--vx-muted) / <alpha-value>)',
        'muted-foreground': 'rgb(var(--vx-muted-fg) / <alpha-value>)',
        accent: 'rgb(var(--vx-accent) / <alpha-value>)',
        'accent-foreground': 'rgb(var(--vx-accent-fg) / <alpha-value>)',
        primary: 'rgb(var(--vx-primary) / <alpha-value>)',
        'primary-foreground': 'rgb(var(--vx-primary-fg) / <alpha-value>)',
        'primary-glow': 'rgb(var(--vx-primary-glow) / <alpha-value>)',
        secondary: 'rgb(var(--vx-secondary) / <alpha-value>)',
        'secondary-foreground': 'rgb(var(--vx-secondary-fg) / <alpha-value>)',
        destructive: 'rgb(var(--vx-destructive) / <alpha-value>)',
        'destructive-foreground': 'rgb(var(--vx-destructive-fg) / <alpha-value>)',
        popover: 'rgb(var(--vx-popover) / <alpha-value>)',
        'popover-foreground': 'rgb(var(--vx-popover-fg) / <alpha-value>)',
        success: 'rgb(var(--vx-success) / <alpha-value>)',
        'success-foreground': 'rgb(var(--vx-success-fg) / <alpha-value>)',
        warning: 'rgb(var(--vx-warning) / <alpha-value>)',
        'warning-foreground': 'rgb(var(--vx-warning-fg) / <alpha-value>)',
        info: 'rgb(var(--vx-info) / <alpha-value>)',
        'info-foreground': 'rgb(var(--vx-info-fg) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['Inter Variable', 'Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', 'Fira Sans', 'Droid Sans', 'Helvetica Neue', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', '"Liberation Mono"', '"Courier New"', 'monospace'],
      },
      borderRadius: {
        xl: '12px',
        '2xl': '16px',
        '3xl': '24px',
      },
      boxShadow: {
        sm: '0 1px 2px 0 rgba(0, 0, 0, 0.05)',
        md: '0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03)',
        card: 'var(--vx-shadow-card)',
        elevated: 'var(--vx-shadow-elevated)',
        glow: '0 0 0 1px rgb(var(--vx-primary) / 0.25), 0 8px 24px -8px rgb(var(--vx-primary) / 0.45)',
      },
      transitionTimingFunction: {
        'out-expo': 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
      animation: {
        'fade-in': 'fadeIn 250ms cubic-bezier(0.16, 1, 0.3, 1) both',
        'fade-in-up': 'fadeInUp 300ms cubic-bezier(0.16, 1, 0.3, 1) both',
        'zoom-in-95': 'zoomIn95 200ms cubic-bezier(0.16, 1, 0.3, 1) both',
        'slide-in-from-left': 'slideInLeft 250ms cubic-bezier(0.16, 1, 0.3, 1) both',
        'slide-in-from-right': 'slideInRight 250ms cubic-bezier(0.16, 1, 0.3, 1) both',
        shimmer: 'shimmer 1.6s linear infinite',
        'pulse-soft': 'pulseSoft 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'progress-indeterminate': 'progressIndeterminate 1.4s ease-in-out infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        fadeInUp: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        zoomIn95: {
          '0%': { transform: 'scale(0.95)', opacity: '0' },
          '100%': { transform: 'scale(1)', opacity: '1' },
        },
        slideInLeft: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(0)' },
        },
        slideInRight: {
          '0%': { transform: 'translateX(100%)' },
          '100%': { transform: 'translateX(0)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        pulseSoft: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.55' },
        },
        progressIndeterminate: {
          '0%': { left: '-33%' },
          '100%': { left: '100%' },
        },
      },
    },
  },
  plugins: [],
}
