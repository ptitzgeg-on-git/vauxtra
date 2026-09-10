/**
 * Restore branch of the wizard: read a backup file in the browser, then hand it to
 * `POST /api/restore` (admin only, rate limited 3/minute) with the export passphrase.
 *
 * The failure path used to print `err.message` — for an axios error that is
 * "Request failed with status code 400", never the reason the backend gave. It now goes
 * through `translateApiError`, which answers in the reader's language.
 */

import { useRef, useState } from 'react';
import { CheckCircle2, Eye, EyeOff, FileJson, Key, Upload } from 'lucide-react';
import { api } from '@/api/client';
import { Button, Field, InlineAlert, Input } from '@/components/ui';
import { translateApiError } from '@/lib/errors';
import { useFormat } from '@/hooks/useFormat';
import { useT } from '@/i18n';
import { SetupStepShell } from './SetupStepShell';

type RestoreSummary = {
  providers: number;
  services: number;
  secretsIncluded: boolean;
  webhooksNeedingUrl: number;
};

interface BackupFileContent {
  version?: string;
  exported_at?: string;
  secrets_included?: boolean;
  providers?: unknown[];
  services?: unknown[];
}

export function RestoreStep({
  onBack,
  onPrepared,
  onFinish,
}: {
  onBack: () => void;
  onPrepared: () => void | Promise<void>;
  onFinish: (summary: RestoreSummary) => void | Promise<void>;
}) {
  const t = useT();
  const { formatDateTime, formatNumber } = useFormat();

  const [backupFile, setBackupFile] = useState<File | null>(null);
  const [backupData, setBackupData] = useState<BackupFileContent | null>(null);
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
      const data = JSON.parse(text) as BackupFileContent;

      if (!data.version) {
        setError(t('setup.restore.error_no_version'));
        setBackupData(null);
        return;
      }

      setBackupData(data);
    } catch {
      setError(t('setup.restore.error_parse'));
      setBackupData(null);
    }
  };

  const handleRestore = async () => {
    if (!backupData || restoring) return;

    if (backupData.secrets_included && !passphrase) {
      setError(t('setup.restore.error_passphrase_required'));
      return;
    }

    setRestoring(true);
    setError('');

    try {
      const result = await api.post<{
        ok: boolean;
        services?: number;
        providers?: number;
        webhooks_needing_url?: number;
      }>('/restore', {
        backup: backupData,
        passphrase: passphrase,
      });
      await onPrepared();
      setRestoreSummary({
        providers: result?.providers ?? backupData.providers?.length ?? 0,
        services: result?.services ?? backupData.services?.length ?? 0,
        secretsIncluded: Boolean(backupData.secrets_included),
        webhooksNeedingUrl: result?.webhooks_needing_url ?? 0,
      });
    } catch (err: unknown) {
      setError(translateApiError(err, t, t('setup.restore.error_failed')));
    } finally {
      setRestoring(false);
    }
  };

  if (restoreSummary) {
    return (
      <SetupStepShell
        icon={<CheckCircle2 />}
        title={t('setup.restore.done_title')}
        description={t('setup.restore.done_subtitle')}
        primary={{
          label: t('setup.restore.open_dashboard'),
          onClick: () => void onFinish(restoreSummary),
        }}
      >
        <dl className="grid grid-cols-2 gap-4">
          <div className="rounded-xl border border-border bg-muted/40 p-4">
            <dt className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t('setup.restore.done_providers')}
            </dt>
            <dd className="nums mt-1 text-2xl font-bold text-foreground">{formatNumber(restoreSummary.providers)}</dd>
          </div>
          <div className="rounded-xl border border-border bg-muted/40 p-4">
            <dt className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t('setup.restore.done_services')}
            </dt>
            <dd className="nums mt-1 text-2xl font-bold text-foreground">{formatNumber(restoreSummary.services)}</dd>
          </div>
        </dl>

        {restoreSummary.secretsIncluded ? (
          <InlineAlert tone="success" title={t('setup.restore.done_secrets_ok')} />
        ) : (
          <InlineAlert tone="warning" title={t('setup.restore.done_secrets_missing_title')}>
            {t('setup.restore.done_secrets_missing')}
          </InlineAlert>
        )}

        {restoreSummary.webhooksNeedingUrl > 0 && (
          <InlineAlert
            tone="warning"
            title={t('setup.restore.done_webhooks_title', { count: restoreSummary.webhooksNeedingUrl })}
          >
            {t('setup.restore.done_webhooks')}
          </InlineAlert>
        )}
      </SetupStepShell>
    );
  }

  return (
    <SetupStepShell
      icon={<Upload />}
      title={t('setup.restore.title')}
      description={t('setup.restore.subtitle')}
      onBack={onBack}
      backDisabled={restoring}
      primary={{
        label: restoring ? t('setup.restore.submitting') : t('setup.restore.submit'),
        onClick: handleRestore,
        disabled: !backupData,
        loading: restoring,
        icon: <Upload />,
      }}
    >
      {/* `sr-only` is clip-based, so this input stays in the tab order unless told otherwise: it
          would be an invisible, unnamed stop right before the drop zone that opens it. The button
          below is the affordance -- it carries the label, the focus ring and the click. */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,application/json"
        onChange={handleFileSelect}
        tabIndex={-1}
        aria-hidden="true"
        className="sr-only"
      />
      <button
        type="button"
        onClick={() => fileInputRef.current?.click()}
        disabled={restoring}
        className="flex w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-border py-8 transition-colors hover:border-primary/50 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
      >
        <span aria-hidden="true" className="grid h-10 w-10 place-items-center rounded-xl bg-muted text-muted-foreground">
          {backupFile ? <FileJson className="h-5 w-5" /> : <Upload className="h-5 w-5" />}
        </span>
        <span className="text-sm font-medium text-foreground">
          {backupFile ? backupFile.name : t('setup.restore.choose_file')}
        </span>
        <span className="text-xs text-muted-foreground">{t('setup.restore.choose_file_hint')}</span>
      </button>

      {backupData && (
        <div className="rounded-xl border border-border bg-muted/40 p-4">
          <p className="text-sm font-semibold text-foreground">{t('setup.restore.info_title')}</p>
          <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-2">
            <div className="flex justify-between gap-3">
              <dt className="text-muted-foreground">{t('setup.restore.info_version')}</dt>
              <dd className="font-mono text-foreground">{backupData.version}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-muted-foreground">{t('setup.restore.info_exported')}</dt>
              <dd className="text-foreground">
                {backupData.exported_at ? formatDateTime(backupData.exported_at) : t('setup.restore.unknown')}
              </dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-muted-foreground">{t('setup.restore.info_providers')}</dt>
              <dd className="nums text-foreground">{formatNumber(backupData.providers?.length ?? 0)}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-muted-foreground">{t('setup.restore.info_services')}</dt>
              <dd className="nums text-foreground">{formatNumber(backupData.services?.length ?? 0)}</dd>
            </div>
            <div className="flex justify-between gap-3 sm:col-span-2">
              <dt className="text-muted-foreground">{t('setup.restore.info_secrets')}</dt>
              <dd className={backupData.secrets_included ? 'inline-flex items-center gap-1 text-success' : 'text-warning'}>
                {backupData.secrets_included ? (
                  <>
                    <Key aria-hidden="true" className="h-3 w-3" />
                    {t('setup.restore.secrets_encrypted')}
                  </>
                ) : (
                  t('setup.restore.secrets_missing')
                )}
              </dd>
            </div>
          </dl>
        </div>
      )}

      {backupData?.secrets_included && (
        <Field
          label={t('setup.restore.passphrase_label')}
          htmlFor="vx-restore-passphrase"
          hint={t('setup.restore.passphrase_hint')}
        >
          <Input
            id="vx-restore-passphrase"
            type={showPassphrase ? 'text' : 'password'}
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
            placeholder={t('setup.restore.passphrase_placeholder')}
            autoComplete="off"
            rightIcon={
              <button
                type="button"
                onClick={() => setShowPassphrase((v) => !v)}
                aria-label={showPassphrase ? t('provider_modal.field.hide_password') : t('provider_modal.field.show_password')}
                aria-pressed={showPassphrase}
                tabIndex={-1}
                className="rounded-md p-0.5 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {showPassphrase ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            }
          />
        </Field>
      )}

      {backupData && !backupData.secrets_included && (
        <InlineAlert tone="warning" title={t('setup.restore.no_secrets_title')}>
          {t('setup.restore.no_secrets_warning')}
        </InlineAlert>
      )}

      {restoring && <InlineAlert tone="info" title={t('setup.restore.in_progress')} />}

      {error && (
        <InlineAlert
          tone="danger"
          title={error}
          onDismiss={() => setError('')}
          action={
            backupData ? (
              <Button size="sm" variant="outline" onClick={handleRestore} disabled={restoring}>
                {t('setup.restore.try_again')}
              </Button>
            ) : undefined
          }
        />
      )}
    </SetupStepShell>
  );
}
