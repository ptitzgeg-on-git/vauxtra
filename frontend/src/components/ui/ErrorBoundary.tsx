import { Component, type ErrorInfo, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { AlertCircle, RefreshCw, RotateCw } from 'lucide-react';
import { useT } from '../../i18n';

export interface ErrorBoundaryLabels {
  title: string;
  body: string;
  retry: string;
  reload: string;
  details: string;
}

interface BaseProps {
  children: ReactNode;
  labels: ErrorBoundaryLabels;
  /** Overrides `labels.title`; kept for the callers that already pass it. */
  fallbackTitle?: string;
  onReset?: () => void;
  /** When this changes while an error is showing, the boundary resets (a route path, an id). */
  resetKey?: unknown;
}

interface State {
  error: Error | null;
}

const FOCUS_RING =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background';

/**
 * The class React needs for `getDerivedStateFromError`. Hooks cannot live here, so the
 * labels arrive already translated from the function wrappers below.
 */
class ErrorBoundaryBase extends Component<BaseProps, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // The boundary swallows the throw; keep it in the console for devtools.
    console.error('[vauxtra] render error', error, info.componentStack);
  }

  componentDidUpdate(prev: BaseProps) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.reset();
  }

  reset = () => {
    this.setState({ error: null });
    this.props.onReset?.();
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    const { labels, fallbackTitle } = this.props;
    return (
      <div
        role="alert"
        className="mx-auto flex max-w-md flex-col items-center justify-center py-24 text-center animate-in fade-in motion-reduce:animate-none"
      >
        <div className="mb-5 flex h-12 w-12 items-center justify-center rounded-2xl border border-destructive/20 bg-destructive/10">
          <AlertCircle className="h-6 w-6 text-destructive" aria-hidden="true" />
        </div>
        <h2 className="text-base font-semibold text-foreground">{fallbackTitle || labels.title}</h2>
        <p className="mt-1.5 text-sm text-muted-foreground">{labels.body}</p>
        {error.message && (
          <details className="mt-4 w-full text-left">
            <summary
              className={`cursor-pointer rounded text-xs font-medium text-muted-foreground hover:text-foreground ${FOCUS_RING}`}
            >
              {labels.details}
            </summary>
            <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {error.message}
            </pre>
          </details>
        )}
        <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
          <button
            type="button"
            onClick={this.reset}
            className={`inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90 ${FOCUS_RING}`}
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            {labels.retry}
          </button>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className={`inline-flex items-center gap-2 rounded-lg border border-border bg-card px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted ${FOCUS_RING}`}
          >
            <RotateCw className="h-4 w-4" aria-hidden="true" />
            {labels.reload}
          </button>
        </div>
      </div>
    );
  }
}

export interface ErrorBoundaryProps {
  children: ReactNode;
  /** Overrides the translated default title. */
  fallbackTitle?: string;
  /** Overrides any of the translated defaults. */
  labels?: Partial<ErrorBoundaryLabels>;
  onReset?: () => void;
  resetKey?: unknown;
}

/**
 * Catches a render error in its subtree and shows a translated fallback with "Try again"
 * (re-renders the subtree) and "Reload page". The rest of the app keeps working.
 */
export function ErrorBoundary({ labels, ...rest }: ErrorBoundaryProps) {
  const t = useT();
  const merged: ErrorBoundaryLabels = {
    title: labels?.title ?? t('ui.error.title'),
    body: labels?.body ?? t('ui.error.body'),
    retry: labels?.retry ?? t('ui.error.retry'),
    reload: labels?.reload ?? t('ui.error.reload'),
    details: labels?.details ?? t('ui.error.details'),
  };
  return <ErrorBoundaryBase labels={merged} {...rest} />;
}

/**
 * One boundary per route: the title names the page, and navigating to another route
 * resets the error so a broken page does not follow the user around.
 */
export function RouteErrorBoundary({ page, children }: { page: string; children: ReactNode }) {
  const t = useT();
  const { pathname } = useLocation();
  return (
    <ErrorBoundary fallbackTitle={t('ui.error.page_unavailable', { page })} resetKey={pathname}>
      {children}
    </ErrorBoundary>
  );
}
