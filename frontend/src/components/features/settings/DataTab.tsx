/**
 * The Settings > Data tab: the four bulk-data screens, composed.
 *
 * Each section owns its own queries, mutations and helpers in `./data/`. They used to share
 * this file -- 1 100 lines in which the Docker discovery table, the sync picker, the backup
 * exporter and the restore reader were interleaved with four unrelated sets of module-level
 * constants, so a change to one meant scrolling past the other three. Only the reset lives
 * here, because it is three lines and belongs to no section.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { AlertTriangle, Trash2 } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { translateApiError } from '@/lib/errors';
import { Button, useConfirmDialog } from '@/components/ui';
import { SectionEyebrow, SettingsSection } from './SettingsSection';
import { DockerSection } from './data/DockerSection';
import { ExportSection } from './data/ExportSection';
import { RestoreSection } from './data/RestoreSection';
import { SyncSection } from './data/SyncSection';

export function DataTab() {
  const t = useT();
  const queryClient = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();

  const resetMutation = useMutation({
    mutationFn: () => api.post<{ ok: boolean }>('/reset'),
    onSuccess: async () => {
      await queryClient.invalidateQueries();
      toast.success(t('settings.data.reset_done'));
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.data.reset_failed'))),
  });

  const requestReset = async () => {
    const ok = await confirm({
      title: t('settings.data.reset_confirm_title'),
      message: t('settings.data.reset_confirm_message'),
      confirmLabel: t('settings.data.reset_cta'),
      variant: 'danger',
      requireText: 'RESET',
    });
    if (ok) resetMutation.mutate();
  };

  return (
    <div className="space-y-6">
      <SyncSection />
      <DockerSection />
      <div className="grid gap-6 xl:grid-cols-2">
        <ExportSection />
        <RestoreSection />
      </div>
      <SettingsSection
        tone="danger"
        icon={<AlertTriangle />}
        title={t('settings.data.reset_title')}
        description={t('settings.data.reset_desc')}
        actions={
          <Button variant="danger" size="sm" leftIcon={<Trash2 />} loading={resetMutation.isPending} onClick={() => void requestReset()}>
            {t('settings.data.reset_cta')}
          </Button>
        }
      >
        <SectionEyebrow>{t('settings.data.danger_zone')}</SectionEyebrow>
        <p className="text-sm text-muted-foreground">{t('settings.data.reset_hint')}</p>
      </SettingsSection>
      {ConfirmDialogElement}
    </div>
  );
}
