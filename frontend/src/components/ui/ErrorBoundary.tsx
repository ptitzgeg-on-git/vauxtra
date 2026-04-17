import { Component, type ReactNode } from 'react';
import { AlertCircle, RefreshCw } from 'lucide-react';

interface Props {
  children: ReactNode;
  fallbackTitle?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <div className="flex flex-col items-center justify-center py-24 max-w-md mx-auto text-center">
        <div className="w-12 h-12 rounded-2xl bg-destructive/10 border border-destructive/20 flex items-center justify-center mb-5">
          <AlertCircle className="w-6 h-6 text-destructive" />
        </div>
        <h3 className="text-base font-semibold text-foreground">
          {this.props.fallbackTitle || 'Something went wrong'}
        </h3>
        <p className="text-sm text-muted-foreground mt-1.5 mb-6">
          This section encountered an error. The rest of the app is still working.
        </p>
        {this.state.error && (
          <pre className="text-xs text-destructive bg-destructive/5 border border-destructive/20 rounded-lg px-4 py-2 mb-4 max-w-full overflow-auto">
            {this.state.error.message}
          </pre>
        )}
        <button
          onClick={() => this.setState({ hasError: false, error: null })}
          className="flex items-center gap-2 text-sm font-semibold text-primary hover:opacity-80 transition-colors bg-card border border-border shadow-sm rounded-lg px-4 py-2"
        >
          <RefreshCw className="w-4 h-4" />
          Try again
        </button>
      </div>
    );
  }
}
