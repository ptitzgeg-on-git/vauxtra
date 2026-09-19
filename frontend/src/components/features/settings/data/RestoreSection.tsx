/**
 * Backup restore -- reads a JSON backup, shows what it holds, then writes it back.
 *
 * Split out of `DataTab.tsx`; see `SyncSection.tsx` for why.
 */

import { useState, type ChangeEvent } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Lock, Upload } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { cn } from '@/lib/cn';
import { translateApiError } from '@/lib/errors';
import { Badge, Button, Field, Input, useConfirmDialog } from '@/components/ui';
import type { RestoreResult } from '@/types/api';
import { SettingsSection } from '../SettingsSection';

type BackupSummary = { services: number; providers: number; domains: number; tags: number; environments: number; webhooks: number; templates: number };

interface PendingRestore {
  json: Record<string, unknown>;
  needsPassphrase: boolean;
  summary: BackupSummary;
}

function summarizeBackup(backup: Record<string, unknown>): BackupSummary {
  const count = (key: string) => (Array.isArray(backup[key]) ? backup[key].length : 0);
  return {
    services: count('services'),
    providers: count('providers'),
    domains: count('domains'),
    tags: count('tags'),
    environments: count('environments'),
    webhooks: count('webhooks'),
    templates: count('service_templates'),
  };
}

const SUMMARY_KEYS: (keyof BackupSummary)[] = ['services', 'providers', 'domains', 'tags', 'environments', 'webhooks', 'templates'];

export function RestoreSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const [pending, setPending] = useState<PendingRestore | null>(null);
  const [passphrase, setPassphrase] = useState('');

  const restore = useMutation({
    mutationFn: (payload: { backup: Record<string, unknown>; passphrase: string }) =>
      api.post<RestoreResult>('/restore', payload),
    onSuccess: async (result) => {
      setPending(null);
      setPassphrase('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['services'] }),
        queryClient.invalidateQueries({ queryKey: ['providers'] }),
        queryClient.invalidateQueries({ queryKey: ['domains'] }),
        queryClient.invalidateQueries({ queryKey: ['tags'] }),
        queryClient.invalidateQueries({ queryKey: ['environments'] }),
        queryClient.invalidateQueries({ queryKey: ['webhooks'] }),
        // The restore empties and refills this table like any other, and the Templates
        // page held whatever was cached from before it: the old rows if the file carried
        // none, the old rows still if it carried different ones.
        queryClient.invalidateQueries({ queryKey: ['templates'] }),
        queryClient.invalidateQueries({ queryKey: ['logs'] }),
        queryClient.invalidateQueries({ queryKey: ['certificates-expiry'] }),
        queryClient.invalidateQueries({ queryKey: ['health'] }),
      ]);
      await Promise.all([
        queryClient.refetchQueries({ queryKey: ['services'], type: 'active' }),
        queryClient.refetchQueries({ queryKey: ['providers'], type: 'active' }),
        queryClient.refetchQueries({ queryKey: ['domains'], type: 'active' }),
        queryClient.refetchQueries({ queryKey: ['tags'], type: 'active' }),
        queryClient.refetchQueries({ queryKey: ['environments'], type: 'active' }),
      ]);
      toast.success(t('settings.backup.restore_success'));
      // A backup without secrets carries no Apprise URL, and the URL *is* the webhook:
      // those rows come back disabled, and the operator has to hear it.
      const needingUrl = result?.webhooks_needing_url ?? 0;
      if (needingUrl > 0) toast.error(t('settings.backup.restore_webhooks_disabled', { count: needingUrl }));
      // Two more outcomes the response has always carried and this handler used to drop on
      // the floor. Neither is a failure -- the restore succeeded -- and neither shows up
      // anywhere else: a setting this version does not accept is simply absent afterwards,
      // and a domain row with no name leaves whatever pointed at it pointing at a domain
      // the list no longer offers. So they are said plainly, and without the red.
      const droppedSettings = result?.settings_not_restored ?? [];
      if (droppedSettings.length > 0) {
        toast(
          t('settings.backup.restore_settings_dropped', {
            count: droppedSettings.length,
            keys: droppedSettings.join(', '),
          }),
        );
      }
      const namelessDomains = result?.domains_without_name ?? 0;
      if (namelessDomains > 0) toast(t('settings.backup.restore_domains_skipped', { count: namelessDomains }));
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.backup.restore_failed'))),
  });

  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const input = e.target;
    const file = input.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const json = JSON.parse(text) as Record<string, unknown> | null;
      if (!json || typeof json !== 'object' || !json.version) {
        toast.error(t('settings.backup.invalid_file'));
        return;
      }
      setPending({ json, needsPassphrase: !!json.secrets_included, summary: summarizeBackup(json) });
      setPassphrase('');
    } catch {
      toast.error(t('settings.backup.invalid_json'));
    } finally {
      input.value = '';
    }
  };

  const requestRestore = async () => {
    if (!pending) return;
    const ok = await confirm({
      title: t('settings.backup.restore_confirm_title'),
      // Each noun is counted in its own language before the sentence is assembled: `t()`
      // inflects exactly one `{count}`, and seven numbers cannot share it.
      message: t(
        'settings.backup.restore_confirm_message',
        Object.fromEntries(
          SUMMARY_KEYS.map((key) => [key, t(`settings.backup.restore_count.${key}`, { count: pending.summary[key] })]),
        ),
      ),
      confirmLabel: t('settings.backup.restore'),
      variant: 'danger',
      // `POST /api/restore` empties the same sixteen tables `POST /api/reset` does, and the
      // reset one button below asks the operator to type RESET. Without this, a file picked
      // by mistake was two clicks from an emptied database.
      requireText: 'RESTORE',
    });
    if (ok) restore.mutate({ backup: pending.json, passphrase });
  };

  return (
    <SettingsSection
      icon={<Upload />}
      title={t('settings.backup.import_title')}
      description={
        <>
          {t('settings.backup.import_desc')}{' '}
          <span className="font-medium text-destructive">{t('settings.backup.import_warning')}</span>
        </>
      }
      className="h-full"
    >
      {!pending ? (
        <div>
          <input id="backup-file" type="file" accept=".json,application/json" className="sr-only" onChange={(e) => void onFile(e)} />
          <label
            htmlFor="backup-file"
            className={cn(
              'inline-flex h-10 cursor-pointer items-center gap-2 rounded-lg border border-border bg-secondary px-4 text-sm font-medium text-secondary-foreground',
              'transition-colors hover:bg-secondary/80 focus-within:ring-2 focus-within:ring-ring',
            )}
          >
            <Upload aria-hidden="true" className="h-4 w-4" />
            {t('settings.backup.choose_file')}
          </label>
        </div>
      ) : (
        <div className="space-y-4 rounded-xl border border-border bg-muted/30 p-4 animate-in fade-in">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium text-foreground">
              {t('settings.backup.version', { version: String(pending.json.version) })}
            </span>
            {pending.needsPassphrase ? (
              <Badge size="sm" tone="primary" icon={<Lock />}>
                {t('settings.backup.encrypted_credentials')}
              </Badge>
            ) : (
              <Badge size="sm" tone="neutral">
                {t('settings.backup.no_credentials')}
              </Badge>
            )}
          </div>

          <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
            {SUMMARY_KEYS.map((key) => (
              <div key={key} className="flex items-baseline justify-between gap-2 rounded-lg border border-border bg-card px-3 py-2">
                <dt className="text-muted-foreground">{t(`settings.backup.summary.${key}`)}</dt>
                <dd className="font-semibold text-foreground tabular-nums">{pending.summary[key]}</dd>
              </div>
            ))}
          </dl>

          {pending.needsPassphrase && (
            <Field label={t('settings.backup.passphrase_used')} required>
              <Input
                type="password"
                autoComplete="off"
                value={passphrase}
                placeholder={t('settings.backup.passphrase_enter')}
                onChange={(e) => setPassphrase(e.target.value)}
                // eslint-disable-next-line jsx-a11y/no-autofocus -- a surface the operator just opened lands focus on its first field
                autoFocus
              />
            </Field>
          )}

          <div className="flex flex-wrap gap-2">
            <Button
              variant="danger"
              leftIcon={<Upload />}
              loading={restore.isPending}
              disabled={pending.needsPassphrase && !passphrase}
              onClick={() => void requestRestore()}
            >
              {restore.isPending ? t('settings.backup.restoring') : t('settings.backup.restore')}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setPending(null);
                setPassphrase('');
              }}
            >
              {t('common.cancel')}
            </Button>
          </div>
        </div>
      )}
      {ConfirmDialogElement}
    </SettingsSection>
  );
}
