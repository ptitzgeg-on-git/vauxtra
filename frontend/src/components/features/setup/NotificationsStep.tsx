/**
 * Optional step: register Apprise webhook targets so Vauxtra can shout when a service goes
 * down. Everything here goes through `useWebhookActions`, which owns the queries, the
 * mutations and their toasts.
 */

import { Bell, ExternalLink, Plus, Send, Trash2 } from 'lucide-react';
import { Button, Field, IconButton, InlineAlert, Input, useConfirmDialog } from '@/components/ui';
import { useWebhookActions } from '@/hooks/useWebhookActions';
import { useT } from '@/i18n';
import { SetupStepShell } from './SetupStepShell';

interface NotificationsStepProps {
  onBack: () => void;
  onContinue: () => void;
}

const APPRISE_URL = 'https://github.com/caronc/apprise';

const EXAMPLES = [
  { key: 'discord', value: 'discord://webhook_id/webhook_token' },
  { key: 'slack', value: 'slack://token_a/token_b/token_c' },
  { key: 'telegram', value: 'tgram://bot_token/chat_id' },
];

export function NotificationsStep({ onBack, onContinue }: NotificationsStepProps) {
  const t = useT();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const {
    webhooks, name, setName, url, setUrl, testResult,
    addWebhook, deleteWebhook, testWebhookById, testWebhookUrl,
  } = useWebhookActions();

  const canAdd = Boolean(name.trim() && url.trim());

  const askDelete = async (id: number, label: string) => {
    const ok = await confirm({
      title: t('setup.notifications.delete_title'),
      message: t('setup.notifications.delete_message', { name: label }),
      confirmLabel: t('common.delete'),
      variant: 'danger',
    });
    if (ok) deleteWebhook.mutate(id);
  };

  return (
    <SetupStepShell
      icon={<Bell />}
      title={t('setup.notifications.title')}
      description={t('setup.notifications.subtitle')}
      onBack={onBack}
      primary={{
        label: webhooks.length > 0 ? t('setup.providers.continue') : t('setup.providers.skip'),
        onClick: onContinue,
      }}
    >
      <p className="rounded-xl border border-border bg-muted/40 p-4 text-sm text-muted-foreground">
        {t('setup.notifications.intro')}{' '}
        <a
          href={APPRISE_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 font-medium text-primary underline-offset-4 hover:underline"
        >
          Apprise
          <ExternalLink aria-hidden="true" className="h-3 w-3" />
        </a>
      </p>

      {webhooks.length > 0 && (
        <ul className="space-y-2">
          {webhooks.map((wh) => (
            <li key={wh.id} className="flex items-center gap-3 rounded-xl border border-border bg-background px-4 py-3">
              <Bell aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-foreground">{wh.name}</span>
                <span className="block truncate font-mono text-xs text-muted-foreground">{wh.url_masked}</span>
              </span>
              <IconButton
                label={t('setup.notifications.send_test')}
                icon={<Send />}
                tooltip
                onClick={() => testWebhookById.mutate(wh.id)}
                disabled={testWebhookById.isPending}
                className="shrink-0 text-muted-foreground"
              />
              <IconButton
                label={t('common.delete')}
                icon={<Trash2 />}
                tooltip
                onClick={() => void askDelete(wh.id, wh.name)}
                disabled={deleteWebhook.isPending}
                className="shrink-0 text-muted-foreground hover:text-destructive"
              />
            </li>
          ))}
        </ul>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Field label={t('setup.notifications.name_label')} htmlFor="vx-setup-webhook-name">
          <Input
            id="vx-setup-webhook-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('setup.notifications.name_placeholder')}
          />
        </Field>
        <Field label={t('setup.notifications.url_label')} htmlFor="vx-setup-webhook-url" className="sm:col-span-2">
          <Input
            id="vx-setup-webhook-url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="discord://webhook_id/webhook_token"
            className="font-mono"
            autoComplete="off"
            spellCheck={false}
          />
        </Field>
      </div>

      {testResult && (
        <InlineAlert
          tone={testResult.ok ? 'success' : 'danger'}
          title={testResult.ok ? t('setup.notifications.test_ok') : t('setup.notifications.test_failed')}
        >
          {testResult.ok ? null : testResult.error}
        </InlineAlert>
      )}

      <div className="flex flex-col gap-2 sm:flex-row">
        <Button
          variant="outline"
          className="flex-1"
          onClick={() => testWebhookUrl.mutate()}
          disabled={!url.trim()}
          loading={testWebhookUrl.isPending}
          leftIcon={<Send />}
        >
          {t('setup.notifications.test')}
        </Button>
        <Button
          className="flex-1"
          onClick={() => addWebhook.mutate(undefined)}
          disabled={!canAdd}
          loading={addWebhook.isPending}
          leftIcon={<Plus />}
        >
          {t('setup.notifications.add')}
        </Button>
      </div>

      <div className="border-t border-border pt-4">
        <p className="text-xs font-semibold text-foreground">{t('setup.notifications.examples_title')}</p>
        <ul className="mt-1.5 space-y-1 font-mono text-xs text-muted-foreground">
          {EXAMPLES.map((example) => (
            <li key={example.key}>
              <span className="font-sans font-medium text-foreground">{t(`setup.notifications.example_${example.key}`)}</span>{' '}
              {example.value}
            </li>
          ))}
        </ul>
      </div>

      {ConfirmDialogElement}
    </SetupStepShell>
  );
}
