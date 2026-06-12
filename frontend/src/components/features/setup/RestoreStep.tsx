import { useState, useRef } from 'react';
import { ArrowLeft, ArrowRight, Loader2, Eye, EyeOff, AlertTriangle, Upload, Key, CheckCircle2 } from 'lucide-react';
import { api } from '@/api/client';

type RestoreSummary = {
  providers: number;
  services: number;
  secretsIncluded: boolean;
};

export function RestoreStep({
  onBack,
  onPrepared,
  onFinish,
}: {
  onBack: () => void;
  onPrepared: () => void | Promise<void>;
  onFinish: (summary: RestoreSummary) => void | Promise<void>;
}) {
  const [backupFile, setBackupFile] = useState<File | null>(null);
  const [backupData, setBackupData] = useState<{
    version?: string;
    exported_at?: string;
    secrets_included?: boolean;
    providers?: unknown[];
    services?: unknown[];
  } | null>(null);
  const [passphrase, setPassphrase] = useState('');
  const [showPassphrase, setShowPassphrase] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState('');
  const [restoreSummary, setRestoreSummary] = useState<RestoreSummary | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    
    setError('');
    setBackupFile(file);
    
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      
      if (!data.version) {
        setError('Invalid backup file: missing version');
        setBackupData(null);
        return;
      }
      
      setBackupData(data);
    } catch {
      setError('Invalid backup file: could not parse JSON');
      setBackupData(null);
    }
  };

  const handleRestore = async () => {
    if (!backupData) return;
    
    if (backupData.secrets_included && !passphrase) {
      setError('This backup contains encrypted secrets. Please enter the passphrase.');
      return;
    }
    
    setRestoring(true);
    setError('');
    
    try {
      const result = await api.post<{ ok: boolean; services?: number; providers?: number }>('/restore', {
        backup: backupData,
        passphrase: passphrase,
      });
      await onPrepared();
      setRestoreSummary({
        providers: result?.providers ?? backupData.providers?.length ?? 0,
        services: result?.services ?? backupData.services?.length ?? 0,
        secretsIncluded: Boolean(backupData.secrets_included),
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Restore failed';
      setError(msg);
    } finally {
      setRestoring(false);
    }
  };

  if (restoreSummary) {
    return (
      <div className="space-y-6 animate-in fade-in duration-300">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-green-500/10 text-green-600 dark:text-green-400 grid place-items-center">
            <CheckCircle2 size={20} />
          </div>
          <div>
            <h2 className="text-xl font-bold text-foreground">Restore completed</h2>
            <p className="text-sm text-muted-foreground">Your configuration has been restored successfully.</p>
          </div>
        </div>

        <div className="bg-card border border-border rounded-xl p-6 space-y-4">
          <ul className="text-sm text-muted-foreground space-y-1">
            <li>Providers restored: {restoreSummary.providers}</li>
            <li>Services restored: {restoreSummary.services}</li>
          </ul>

          {restoreSummary.secretsIncluded ? (
            <div className="rounded-lg border border-green-500/30 bg-green-500/10 p-3 text-xs text-green-700 dark:text-green-300">
              Secure backup detected: provider secrets were restored from encrypted data.
            </div>
          ) : (
            <div className="rounded-lg border border-yellow-500/30 bg-yellow-500/10 p-3 text-xs text-yellow-700 dark:text-yellow-300">
              Secrets were not included in this backup. Re-enter provider passwords and tokens before running health checks.
            </div>
          )}
        </div>

        <div className="flex justify-end">
          <button
            onClick={() => onFinish(restoreSummary)}
            className="inline-flex items-center gap-2 bg-primary text-primary-foreground hover:opacity-90 px-6 py-2.5 rounded-xl text-sm font-semibold transition-all"
          >
            Open dashboard
            <ArrowRight size={16} />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary grid place-items-center">
          <Upload size={20} />
        </div>
        <div>
          <h2 className="text-xl font-bold text-foreground">Restore from Backup</h2>
          <p className="text-sm text-muted-foreground">Upload a Vauxtra backup file to restore your configuration.</p>
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-6 space-y-4">
        <div>
          <input
            ref={fileInputRef}
            type="file"
            accept=".json"
            onChange={handleFileSelect}
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            className="w-full flex items-center justify-center gap-2 border-2 border-dashed border-border rounded-xl py-8 hover:border-primary hover:bg-primary/5 transition-all"
          >
            <Upload size={20} className="text-muted-foreground" />
            <span className="text-sm text-muted-foreground">
              {backupFile ? backupFile.name : 'Click to select backup file'}
            </span>
          </button>
        </div>

        {backupData && (
          <div className="bg-muted/50 rounded-lg p-4 space-y-2">
            <p className="text-sm font-medium text-foreground">Backup Info</p>
            <ul className="text-xs text-muted-foreground space-y-1">
              <li>Version: {backupData.version}</li>
              <li>Exported: {backupData.exported_at ? new Date(backupData.exported_at).toLocaleString() : 'Unknown'}</li>
              <li>Providers: {backupData.providers?.length || 0}</li>
              <li>Services: {backupData.services?.length || 0}</li>
              <li className="flex items-center gap-1">
                Secrets: {backupData.secrets_included ? (
                  <span className="text-primary flex items-center gap-1"><Key size={12} /> Encrypted</span>
                ) : (
                  <span className="text-yellow-600">Not included</span>
                )}
              </li>
            </ul>
          </div>
        )}

        {backupData?.secrets_included && (
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground flex items-center gap-2">
              <Key size={14} />
              Backup Passphrase
            </label>
            <div className="relative">
              <input
                type={showPassphrase ? 'text' : 'password'}
                value={passphrase}
                onChange={(e) => setPassphrase(e.target.value)}
                placeholder="Enter the passphrase used during export"
                className="w-full bg-input border border-border rounded-lg px-4 py-2.5 text-sm pr-10"
              />
              <button
                type="button"
                onClick={() => setShowPassphrase(!showPassphrase)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                {showPassphrase ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            <p className="text-xs text-muted-foreground">
              Required to decrypt provider passwords and Docker TLS certificates.
            </p>
          </div>
        )}

        {backupData && !backupData.secrets_included && (
          <div className="flex items-start gap-2 bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-3">
            <AlertTriangle size={16} className="text-yellow-600 shrink-0 mt-0.5" />
            <p className="text-xs text-yellow-600">
              This backup does not include secrets. After restore, you will need to re-enter passwords for all providers.
            </p>
          </div>
        )}

        {restoring && (
          <div className="flex items-start gap-2 bg-primary/10 border border-primary/30 rounded-lg p-3">
            <Loader2 size={16} className="text-primary shrink-0 mt-0.5 animate-spin" />
            <p className="text-xs text-primary">
              Restore in progress. Do not close this page. Large backups can take a little time to finish.
            </p>
          </div>
        )}

        {error && (
          <div className="flex items-start gap-2 bg-destructive/10 border border-destructive/30 rounded-lg p-3">
            <AlertTriangle size={16} className="text-destructive shrink-0 mt-0.5" />
            <p className="text-xs text-destructive">{error}</p>
          </div>
        )}
      </div>

      <div className="flex justify-between">
        <button
          onClick={onBack}
          disabled={restoring}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft size={14} />
          Back
        </button>
        <button
          onClick={handleRestore}
          disabled={!backupData || restoring}
          className="inline-flex items-center gap-2 bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 px-6 py-2.5 rounded-xl text-sm font-semibold transition-all"
        >
          {restoring ? (
            <>
              <Loader2 size={16} className="animate-spin" />
              Restoring...
            </>
          ) : (
            <>
              Restore Backup
              <ArrowRight size={16} />
            </>
          )}
        </button>
      </div>
    </div>
  );
}
