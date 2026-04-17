import { Monitor, Moon, Sun, Globe } from 'lucide-react';
import type { UseMutationResult } from '@tanstack/react-query';

interface GeneralTabProps {
  theme: string;
  resolvedTheme: string;
  setTheme: (t: string) => void;
  settingsData: Record<string, string> | undefined;
  savePolicyMutation: UseMutationResult<unknown, unknown, Record<string, string>, unknown>;
}

export function GeneralTab({ theme, resolvedTheme, setTheme, settingsData, savePolicyMutation }: GeneralTabProps) {
  return (
    <div className="space-y-6">
      <div className="bg-card border border-border rounded-xl p-6 shadow-sm">
        <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
          <Monitor className="w-5 h-5 text-muted-foreground" />
          Appearance & UI
        </h3>
        <div className="flex items-center justify-between py-4 border-b border-border">
          <div>
            <p className="font-medium">Theme mode</p>
            <p className="text-sm text-muted-foreground">Choose Light, Dark, or Auto (system preference).</p>
          </div>
          <div className="flex items-center gap-2">
            {(['light', 'dark', 'system'] as const).map((t) => {
              const Icon = t === 'light' ? Sun : t === 'dark' ? Moon : Monitor;
              const label = t === 'system' ? 'Auto' : t.charAt(0).toUpperCase() + t.slice(1);
              return (
                <button
                  key={t}
                  type="button"
                  onClick={() => setTheme(t)}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border text-xs font-semibold ${
                    theme === t
                      ? 'bg-primary text-primary-foreground border-primary'
                      : 'bg-background text-muted-foreground border-border hover:bg-accent'
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" />
                  {label}
                </button>
              );
            })}
          </div>
        </div>
        <div className="pt-4 text-sm text-muted-foreground">
          Active rendering mode: <span className="font-semibold text-foreground">{resolvedTheme}</span>
          {theme === 'system' && <span> (driven by machine preference)</span>}
        </div>
      </div>

      <form
        className="bg-card border border-border rounded-xl p-6 shadow-sm space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          savePolicyMutation.mutate({
            public_target_sources: String(fd.get('public_target_sources') || '').trim(),
            public_target_timeout: String(fd.get('public_target_timeout') || '').trim(),
            public_target_priority: String(fd.get('public_target_priority') || '').trim(),
          });
        }}
      >
        <h3 className="font-semibold text-lg mb-2 flex items-center gap-2">
          <Globe className="w-5 h-5 text-muted-foreground" />
          WAN Detection Policy
        </h3>
        <p className="text-sm text-muted-foreground">
          Configure how automatic public target detection resolves your public WAN endpoint.
        </p>

        <div>
          <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider block mb-2">
            Resolver sources (one URL per line)
          </label>
          <textarea
            name="public_target_sources"
            rows={4}
            defaultValue={settingsData?.public_target_sources || 'https://api.ipify.org\nhttps://ifconfig.me/ip\nhttps://icanhazip.com'}
            className="w-full bg-input border border-border rounded-md px-3 py-2 text-sm font-mono"
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider block mb-2">
              Timeout per source (seconds)
            </label>
            <input type="number" min="0.5" max="10" step="0.1" name="public_target_timeout"
              defaultValue={settingsData?.public_target_timeout || '2.0'}
              className="w-full bg-input border border-border rounded-md px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider block mb-2">
              Priority order
            </label>
            <input type="text" name="public_target_priority"
              defaultValue={settingsData?.public_target_priority || 'server_public_ip,proxy_provider_host,current'}
              className="w-full bg-input border border-border rounded-md px-3 py-2 text-sm font-mono" />
            <p className="text-xs text-muted-foreground mt-1">Allowed values: server_public_ip, proxy_provider_host, current</p>
          </div>
        </div>

        <div className="pt-2">
          <button type="submit" disabled={savePolicyMutation.isPending}
            className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm hover:opacity-90 disabled:opacity-60">
            {savePolicyMutation.isPending ? 'Saving policy...' : 'Save WAN policy'}
          </button>
        </div>
      </form>

      <form
        className="bg-card border border-border rounded-xl p-6 shadow-sm space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          const enabled = (fd.get('auto_check') as string) === 'on';
          const interval = Number(fd.get('check_interval') || 5);
          savePolicyMutation.mutate({ check_interval: enabled ? String(Math.max(1, interval)) : '0' });
        }}
      >
        <h3 className="font-semibold text-lg mb-2 flex items-center gap-2">
          <Monitor className="w-5 h-5 text-muted-foreground" />
          Automatic Health Checks
        </h3>
        <p className="text-sm text-muted-foreground">
          When enabled, Vauxtra checks all active services on a scheduled interval and sends a webhook notification on status change.
        </p>
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4">
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input type="checkbox" name="auto_check"
              defaultChecked={!!settingsData?.check_interval && settingsData.check_interval !== '0'}
              className="rounded border-border" />
            <span className="text-sm font-medium">Enable automatic monitoring</span>
          </label>
          <div className="flex items-center gap-2">
            <label className="text-xs text-muted-foreground">Interval (minutes)</label>
            <input type="number" name="check_interval" min="1" max="60"
              defaultValue={settingsData?.check_interval && settingsData.check_interval !== '0' ? settingsData.check_interval : '5'}
              className="w-20 bg-input border border-border rounded-md px-3 py-1.5 text-sm" />
          </div>
        </div>
        <div className="pt-1">
          <button type="submit" disabled={savePolicyMutation.isPending}
            className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm hover:opacity-90 disabled:opacity-60">
            {savePolicyMutation.isPending ? 'Saving...' : 'Save monitoring settings'}
          </button>
        </div>
      </form>
    </div>
  );
}
