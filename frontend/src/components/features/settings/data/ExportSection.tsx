/**
 * Backup export -- the whole database as a JSON file, optionally encrypted with a passphrase.
 *
 * Split out of `DataTab.tsx`; see `SyncSection.tsx` for why.
 */

import { useState, type FormEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { DownloadCloud, Lock } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { decodeBlobErrorBody, getErrorMessage, translateApiError } from '@/lib/errors';
import { MIN_PASSWORD_DISTINCT_CHARS, MIN_PASSWORD_LENGTH, isPasswordStrongEnough } from '@/constants';
import { Button, Field, Input } from '@/components/ui';
import { SettingsSection } from '../SettingsSection';

/** Hands the browser a file to save; the object URL is released once the click is dispatched. */
function downloadBlob(data: unknown, filename: string) {
  const url = window.URL.createObjectURL(new Blob([data as BlobPart]));
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

function backupFilename(secure: boolean): string {
  const stamp = new Date().toISOString().split('T')[0];
  return secure ? `vauxtra-backup-secure-${stamp}.json` : `vauxtra-backup-${stamp}.json`;
}


export function ExportSection() {
  const t = useT();
  const [showSecure, setShowSecure] = useState(false);
  const [passphrase, setPassphrase] = useState('');

  /**
   * `responseType: 'blob'` applies to the error body too, so the reason the export failed
   * arrives as a Blob no reader can see into. Decode it first, then show the translated
   * sentence and, underneath, whatever the API said -- for a refused passphrase or a
   * half-written database, that line is the only thing that says what to do next.
   */
  const reportExportError = (err: unknown) => {
    void decodeBlobErrorBody(err).then((decoded) => {
      const sentence = translateApiError(decoded, t, t('settings.backup.export_failed'));
      const detail = getErrorMessage(decoded, '');
      toast.error(detail && detail !== sentence ? `${sentence}\n${detail}` : sentence);
    });
  };

  const plainExport = useMutation({
    mutationFn: () => api.get<Blob>('/backup', { responseType: 'blob' }),
    onSuccess: (data) => {
      downloadBlob(data, backupFilename(false));
      toast.success(t('settings.backup.export_success'));
    },
    onError: reportExportError,
  });

  const secureExport = useMutation({
    mutationFn: (value: string) => api.post<Blob>('/backup/secure', { passphrase: value }, { responseType: 'blob' }),
    onSuccess: (data) => {
      downloadBlob(data, backupFilename(true));
      setShowSecure(false);
      setPassphrase('');
      toast.success(t('settings.backup.export_secure_success'));
    },
    onError: reportExportError,
  });

  const tooShort = passphrase.length > 0 && passphrase.length < MIN_PASSWORD_LENGTH;
  // The archive is only as strong as this: `app/security.py` refuses a passphrase built out
  // of a handful of characters, and so does the form, before the export runs.
  const tooPlain =
    passphrase.length >= MIN_PASSWORD_LENGTH && new Set(passphrase).size < MIN_PASSWORD_DISTINCT_CHARS;

  const submitSecure = (e: FormEvent) => {
    e.preventDefault();
    if (passphrase.length < MIN_PASSWORD_LENGTH) {
      toast.error(t('settings.backup.passphrase_min', { min: MIN_PASSWORD_LENGTH }));
      return;
    }
    if (!isPasswordStrongEnough(passphrase)) {
      toast.error(t('settings.backup.passphrase_distinct', { count: MIN_PASSWORD_DISTINCT_CHARS }));
      return;
    }
    secureExport.mutate(passphrase);
  };

  return (
    <SettingsSection
      icon={<DownloadCloud />}
      title={t('settings.backup.export_title')}
      description={t('settings.backup.export_desc')}
      className="h-full"
    >
      <div className="flex flex-wrap gap-2">
        <Button leftIcon={<DownloadCloud />} loading={plainExport.isPending} onClick={() => plainExport.mutate()}>
          {t('settings.backup.export_plain')}
        </Button>
        <Button variant="secondary" leftIcon={<Lock />} aria-expanded={showSecure} onClick={() => setShowSecure((v) => !v)}>
          {t('settings.backup.export_secure')}
        </Button>
      </div>

      {showSecure && (
        <form onSubmit={submitSecure} className="space-y-3 rounded-xl border border-border bg-muted/30 p-4 animate-in fade-in">
          <p className="text-xs text-muted-foreground">{t('settings.backup.secure_export_help')}</p>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
            <Field
              label={t('settings.backup.passphrase_label')}
              required
              className="min-w-0 flex-1"
              error={
                tooPlain
                  ? t('settings.backup.passphrase_distinct', { count: MIN_PASSWORD_DISTINCT_CHARS })
                  : tooShort
                    ? t('settings.backup.passphrase_min', { min: MIN_PASSWORD_LENGTH })
                    : undefined
              }
            >
              <Input
                type="password"
                autoComplete="new-password"
                value={passphrase}
                minLength={MIN_PASSWORD_LENGTH}
                placeholder={t('settings.backup.passphrase_placeholder', { min: MIN_PASSWORD_LENGTH })}
                onChange={(e) => setPassphrase(e.target.value)}
              />
            </Field>
            <Button
              type="submit"
              leftIcon={<Lock />}
              loading={secureExport.isPending}
              disabled={!isPasswordStrongEnough(passphrase)}
              className="sm:mt-5.5"
            >
              {t('settings.backup.export')}
            </Button>
          </div>
        </form>
      )}

      <dl className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
        <div className="rounded-lg border border-border bg-muted/30 px-3 py-2">
          <dt className="font-semibold text-foreground">{t('settings.backup.no_credentials')}</dt>
          <dd>{t('settings.backup.no_credentials_desc')}</dd>
        </div>
        <div className="rounded-lg border border-border bg-muted/30 px-3 py-2">
          <dt className="font-semibold text-foreground">{t('settings.backup.with_credentials')}</dt>
          <dd>{t('settings.backup.with_credentials_desc')}</dd>
        </div>
      </dl>
    </SettingsSection>
  );
}

// ─── Restore ──────────────────────────────────────────────────────────────────
