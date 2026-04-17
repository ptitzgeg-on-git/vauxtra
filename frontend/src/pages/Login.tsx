import { useState } from 'react';
import { Lock } from 'lucide-react';
import { api } from '@/api/client';

interface LoginPageProps {
  onSuccess: () => void;
}

export function Login({ onSuccess }: LoginPageProps) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await api.post('/auth/login', { password });
      onSuccess();
    } catch {
      setError('Invalid password');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm space-y-8">
        <div className="text-center">
          <div className="mx-auto w-14 h-14 rounded-2xl bg-primary/10 flex items-center justify-center mb-4">
            <Lock className="w-7 h-7 text-primary" />
          </div>
          <h1 className="text-2xl font-bold text-foreground">Vauxtra</h1>
          <p className="text-sm text-muted-foreground mt-1">Enter your password to continue</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              autoFocus
              required
              className="w-full bg-card border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground outline-none transition-all shadow-sm"
            />
          </div>

          {error && (
            <p className="text-sm text-destructive text-center">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading || !password}
            className="w-full bg-primary text-primary-foreground py-3 rounded-lg text-sm font-semibold hover:opacity-90 disabled:opacity-60 transition-all shadow-sm"
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>

          <p className="text-xs text-muted-foreground text-center">
            Forgot your password? See the{' '}
            <a
              href="https://github.com/ptitzgeg-on-git/vauxtra/blob/main/docs/HOWTO.md#2-authentication"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              documentation
            </a>{' '}
            for recovery options.
          </p>
        </form>
      </div>
    </div>
  );
}
