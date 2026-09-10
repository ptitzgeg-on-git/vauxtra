/**
 * Optional step: register Docker engines so Vauxtra can discover containers.
 * `useDockerEndpoints` owns the query key `['docker-endpoints']` and both mutations.
 */

import { Container, Plus, Trash2 } from 'lucide-react';
import { Button, Field, IconButton, Input, useConfirmDialog } from '@/components/ui';
import { useDockerEndpoints } from '@/hooks/useDockerEndpoints';
import { useT } from '@/i18n';
import { SetupStepShell } from './SetupStepShell';

interface DockerStepProps {
  onBack: () => void;
  onContinue: () => void;
}

const SCHEMES = [
  { key: 'local', sample: 'unix:///var/run/docker.sock' },
  { key: 'tcp', sample: 'tcp://192.168.1.10:2375' },
  { key: 'ssh', sample: 'ssh://user@host' },
];

export function DockerStep({ onBack, onContinue }: DockerStepProps) {
  const t = useT();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const { endpoints, name, setName, host, setHost, canSubmit, addEndpoint, deleteEndpoint } = useDockerEndpoints();

  const askDelete = async (id: number, label: string) => {
    const ok = await confirm({
      title: t('setup.docker.delete_title'),
      message: t('setup.docker.delete_message', { name: label }),
      confirmLabel: t('common.delete'),
      variant: 'danger',
    });
    if (ok) deleteEndpoint.mutate(id);
  };

  return (
    <SetupStepShell
      icon={<Container />}
      title={t('setup.docker.title')}
      description={t('setup.docker.subtitle')}
      onBack={onBack}
      primary={{
        label: endpoints.length > 0 ? t('setup.providers.continue') : t('setup.providers.skip'),
        onClick: onContinue,
      }}
    >
      <div className="space-y-3 rounded-xl border border-border bg-muted/40 p-4">
        <p className="text-sm text-muted-foreground">{t('setup.docker.intro')}</p>
        <ul className="space-y-2 text-xs text-muted-foreground">
          {SCHEMES.map((scheme) => (
            <li key={scheme.key}>
              <span className="font-semibold text-foreground">{t(`setup.docker.scheme_${scheme.key}`)}</span>
              <code className="ml-1.5 rounded bg-muted px-1.5 py-0.5 font-mono text-foreground">{scheme.sample}</code>
              <span className="ml-1.5">{t(`setup.docker.scheme_${scheme.key}_hint`)}</span>
            </li>
          ))}
        </ul>
      </div>

      {endpoints.length > 0 && (
        <ul className="space-y-2">
          {endpoints.map((endpoint) => (
            <li key={endpoint.id} className="flex items-center gap-3 rounded-xl border border-border bg-background px-4 py-3">
              <Container aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-foreground">{endpoint.name}</span>
                <span className="block truncate font-mono text-xs text-muted-foreground">{endpoint.docker_host}</span>
              </span>
              <IconButton
                label={t('common.delete')}
                icon={<Trash2 />}
                tooltip
                onClick={() => void askDelete(endpoint.id, endpoint.name)}
                disabled={deleteEndpoint.isPending}
                className="shrink-0 text-muted-foreground hover:text-destructive"
              />
            </li>
          ))}
        </ul>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label={t('provider_modal.docker.name')} htmlFor="vx-setup-docker-name">
          <Input
            id="vx-setup-docker-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('provider_modal.docker.name_placeholder')}
          />
        </Field>
        <Field label={t('provider_modal.docker.host')} htmlFor="vx-setup-docker-host">
          <Input
            id="vx-setup-docker-host"
            value={host}
            onChange={(e) => setHost(e.target.value)}
            placeholder="unix:///var/run/docker.sock"
            className="font-mono"
            autoComplete="off"
            spellCheck={false}
          />
        </Field>
      </div>

      <Button
        variant="outline"
        className="w-full"
        onClick={() => addEndpoint.mutate()}
        disabled={!canSubmit}
        loading={addEndpoint.isPending}
        leftIcon={<Plus />}
      >
        {t('provider_modal.docker.submit')}
      </Button>

      {ConfirmDialogElement}
    </SetupStepShell>
  );
}
