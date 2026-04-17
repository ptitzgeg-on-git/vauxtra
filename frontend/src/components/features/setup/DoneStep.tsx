import { Lock, GitMerge, Bell, Container, Globe, CheckCircle2 } from 'lucide-react';
import { useDockerEndpoints } from '@/hooks/useDockerEndpoints';
import { useWebhookActions } from '@/hooks/useWebhookActions';
import type { ProviderItem } from './types';

interface DoneStepProps {
  skipPassword: boolean | null;
  providers: ProviderItem[];
  onFinish: () => void;
}

export function DoneStep({ skipPassword, providers, onFinish }: DoneStepProps) {
  const { endpoints } = useDockerEndpoints();
  const { webhooks } = useWebhookActions();
  return (
    <div className="text-center space-y-6 animate-in fade-in duration-500">
      <div className="w-20 h-20 rounded-full bg-primary/10 text-primary grid place-items-center mx-auto">
        <CheckCircle2 size={40} />
      </div>
      <div>
        <h2 className="text-2xl font-bold text-foreground">You're all set!</h2>
        <p className="text-muted-foreground mt-2">
          Vauxtra is ready to manage your services.
        </p>
      </div>

      <div className="bg-card border border-border rounded-xl p-5 text-left max-w-md mx-auto">
        <p className="text-sm font-medium text-foreground mb-3">Setup summary:</p>
        <ul className="text-sm space-y-2 text-muted-foreground">
          <li className="flex items-center gap-2">
            <Lock size={14} className={skipPassword ? 'text-yellow-500' : 'text-primary'} />
            {skipPassword ? 'Open access (no password)' : 'Password configured'}
          </li>
          <li className="flex items-center gap-2">
            <GitMerge size={14} className={providers.length > 0 ? 'text-primary' : 'text-muted-foreground/40'} />
            {providers.length > 0 ? `${providers.length} provider${providers.length > 1 ? 's' : ''} connected` : 'No providers yet'}
          </li>
          <li className="flex items-center gap-2">
            <Bell size={14} className={webhooks.length > 0 ? 'text-primary' : 'text-muted-foreground/40'} />
            {webhooks.length > 0 ? `${webhooks.length} webhook${webhooks.length > 1 ? 's' : ''} configured` : 'No notifications'}
          </li>
          <li className="flex items-center gap-2">
            <Container size={14} className={endpoints.length > 0 ? 'text-primary' : 'text-muted-foreground/40'} />
            {endpoints.length > 0 ? `${endpoints.length} Docker endpoint${endpoints.length > 1 ? 's' : ''}` : 'No Docker endpoints'}
          </li>
        </ul>
      </div>

      <div className="flex flex-col items-center gap-3">
        <button
          onClick={onFinish}
          className="inline-flex items-center gap-2 bg-primary text-primary-foreground hover:opacity-90 px-8 py-3.5 rounded-xl text-sm font-semibold transition-all shadow-md"
        >
          <Globe size={18} />
          Go to Dashboard
        </button>
        <p className="text-xs text-muted-foreground">
          You can manage all settings from the Settings page at any time.
        </p>
      </div>
    </div>
  );
}
