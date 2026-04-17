import {
  ArrowLeft, ArrowRight, Bell, Plus, Trash2, Loader2, Send,
  CheckCircle2, AlertTriangle, ExternalLink,
} from 'lucide-react';
import { useWebhookActions } from '@/hooks/useWebhookActions';

interface NotificationsStepProps {
  onBack: () => void;
  onContinue: () => void;
}

export function NotificationsStep({ onBack, onContinue }: NotificationsStepProps) {
  const {
    webhooks, name, setName, url, setUrl, testResult,
    addWebhook, deleteWebhook, testWebhookById, testWebhookUrl,
  } = useWebhookActions();

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary grid place-items-center">
          <Bell size={20} />
        </div>
        <div>
          <h2 className="text-xl font-bold text-foreground">Notifications</h2>
          <p className="text-sm text-muted-foreground">Get alerts when services go down or drift is detected.</p>
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-6 space-y-5">
        <div className="bg-muted/50 border border-border rounded-lg p-4">
          <p className="text-sm text-muted-foreground">
            Vauxtra can send notifications via <strong>webhooks</strong> (Discord, Slack, Telegram, etc.) using <a href="https://github.com/caronc/apprise" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline inline-flex items-center gap-1">Apprise <ExternalLink size={12} /></a> format.
          </p>
        </div>

        {webhooks.length > 0 && (
          <div className="space-y-2">
            {webhooks.map((wh) => (
              <div key={wh.id} className="flex items-center gap-3 px-4 py-3 rounded-xl bg-background border border-border">
                <Bell size={16} className="text-primary shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium">{wh.name}</p>
                  <p className="text-xs text-muted-foreground font-mono truncate">{wh.url}</p>
                </div>
                <button onClick={() => testWebhookById.mutate(wh.id)} className="text-muted-foreground hover:text-primary transition-colors shrink-0" title="Send test notification">
                  <Send size={14} />
                </button>
                <button onClick={() => deleteWebhook.mutate(wh.id)} className="text-muted-foreground hover:text-destructive transition-colors shrink-0">
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name (e.g. Discord)"
              className="bg-background border border-input rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="discord://webhook_id/webhook_token"
              className="sm:col-span-2 bg-background border border-input rounded-lg px-4 py-3 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          
          {testResult && (
            <div className={`p-3 rounded-lg border ${testResult.ok ? 'bg-green-500/5 border-green-500/30' : 'bg-destructive/5 border-destructive/30'}`}>
              <div className="flex items-center gap-2">
                {testResult.ok ? (
                  <CheckCircle2 size={14} className="text-green-600 dark:text-green-400" />
                ) : (
                  <AlertTriangle size={14} className="text-destructive" />
                )}
                <span className={`text-sm ${testResult.ok ? 'text-green-600 dark:text-green-400' : 'text-destructive'}`}>
                  {testResult.ok ? 'Test successful! You can now add the webhook.' : testResult.error}
                </span>
              </div>
            </div>
          )}
          
          <div className="flex gap-2">
            <button
              onClick={() => testWebhookUrl.mutate()}
              disabled={testWebhookUrl.isPending || !url.trim()}
              className="flex-1 flex items-center justify-center gap-2 border border-border rounded-xl py-3 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-all disabled:opacity-50"
            >
              {testWebhookUrl.isPending ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
              Test notification
            </button>
            <button
              onClick={() => addWebhook.mutate()}
              disabled={addWebhook.isPending || !name.trim() || !url.trim()}
              className="flex-1 flex items-center justify-center gap-2 bg-primary text-primary-foreground hover:opacity-90 rounded-xl py-3 text-sm font-semibold transition-all disabled:opacity-50"
            >
              {addWebhook.isPending ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
              Add webhook
            </button>
          </div>
        </div>

        <div className="pt-2 border-t border-border text-xs text-muted-foreground">
          <p><strong>Examples:</strong></p>
          <ul className="mt-1 space-y-0.5 font-mono">
            <li>Discord: discord://webhook_id/webhook_token</li>
            <li>Slack: slack://token_a/token_b/token_c</li>
            <li>Telegram: tgram://bot_token/chat_id</li>
          </ul>
        </div>
      </div>

      <div className="flex justify-between">
        <button onClick={onBack} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
          <ArrowLeft size={14} /> Back
        </button>
        <button onClick={onContinue} className="inline-flex items-center gap-2 bg-primary text-primary-foreground hover:opacity-90 px-6 py-2.5 rounded-xl text-sm font-semibold transition-all">
          {webhooks.length > 0 ? 'Continue' : 'Skip for now'}
          <ArrowRight size={16} />
        </button>
      </div>
    </div>
  );
}
