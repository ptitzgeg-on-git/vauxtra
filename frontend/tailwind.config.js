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
        border: 'rgb(var(--vx-border) / <alpha-value>)',
        input: 'rgb(var(--vx-input) / <alpha-value>)',
        muted: 'rgb(var(--vx-muted) / <alpha-value>)',
        'muted-foreground': 'rgb(var(--vx-muted-fg) / <alpha-value>)',
        accent: 'rgb(var(--vx-accent) / <alpha-value>)',
        'accent-foreground': 'rgb(var(--vx-accent-fg) / <alpha-value>)',
        primary: 'rgb(var(--vx-primary) / <alpha-value>)',
        'primary-foreground': 'rgb(var(--vx-primary-fg) / <alpha-value>)',
        secondary: 'rgb(var(--vx-secondary) / <alpha-value>)',
        'secondary-foreground': 'rgb(var(--vx-secondary-fg) / <alpha-value>)',
        destructive: 'rgb(var(--vx-destructive) / <alpha-value>)',
        'destructive-foreground': 'rgb(var(--vx-destructive-fg) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', 'Fira Sans', 'Droid Sans', 'Helvetica Neue', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', '"Liberation Mono"', '"Courier New"', 'monospace'],
      },
      boxShadow: {
        'sm': '0 1px 2px 0 rgba(0, 0, 0, 0.05)',
        'md': '0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03)',
      },
      animation: {
        'fade-in': 'fadeIn 300ms ease-out',
        'zoom-in-95': 'zoomIn95 300ms ease-out',
        'slide-in-from-left': 'slideInLeft 300ms ease-out'
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        zoomIn95: {
          '0%': { transform: 'scale(0.95)', opacity: '0' },
          '100%': { transform: 'scale(1)', opacity: '1' }
        },
        slideInLeft: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(0)' }
        }
      }
    },
  },
  plugins: [],
}
