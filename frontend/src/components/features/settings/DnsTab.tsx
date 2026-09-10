import { useMemo, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Globe, Plus, Trash2 } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { translateApiError } from '@/lib/errors';
import {
  Badge,
  Button,
  EmptyState,
  Field,
  IconButton,
  InlineAlert,
  Input,
  SearchInput,
  SkeletonRow,
  useConfirmDialog,
} from '@/components/ui';
import type { Service } from '@/types/api';
import { SettingsSection } from './SettingsSection';

// `POST /api/domains` runs `is_valid_domain(..., require_dot=True)` (`app/api/settings.py`),
// so a single label (`lan`, `home`) is refused by the API: at least one dot, or the form
// would accept what the request then rejects.
const DOMAIN_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/;
const SEARCH_THRESHOLD = 6;

/** Root domains shared by every service and DNS provider. */
export function DnsTab() {
  const t = useT();
  const queryClient = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();

  const [newDomain, setNewDomain] = useState('');
  const [search, setSearch] = useState('');

  const domainsQuery = useQuery<string[]>({
    queryKey: ['domains'],
    queryFn: () => api.get<string[]>('/domains'),
  });
  const { data: services = [] } = useQuery<Service[]>({
    queryKey: ['services'],
    queryFn: () => api.get<Service[]>('/services'),
  });

  const domains = useMemo(() => domainsQuery.data ?? [], [domainsQuery.data]);
  const usage = useMemo(() => {
    const counts = new Map<string, number>();
    for (const service of services) counts.set(service.domain, (counts.get(service.domain) ?? 0) + 1);
    return counts;
  }, [services]);

  const addDomain = useMutation({
    mutationFn: (name: string) => api.post('/domains', { name }),
    onSuccess: (_data, name) => {
      queryClient.invalidateQueries({ queryKey: ['domains'] });
      setNewDomain('');
      toast.success(t('settings.dns.added', { domain: name }));
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.dns.add_failed'))),
  });

  const deleteDomain = useMutation({
    mutationFn: (domain: string) => api.delete(`/domains/${encodeURIComponent(domain)}`),
    onSuccess: (_data, domain) => {
      queryClient.invalidateQueries({ queryKey: ['domains'] });
      toast.success(t('settings.dns.deleted', { domain }));
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.dns.delete_failed'))),
  });

  const candidate = newDomain.trim().toLowerCase();
  const candidateInvalid = candidate !== '' && !DOMAIN_RE.test(candidate);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!candidate || candidateInvalid) return;
    if (domains.includes(candidate)) {
      toast.error(t('settings.dns.exists', { domain: candidate }));
      return;
    }
    addDomain.mutate(candidate);
  };

  const requestDelete = async (domain: string) => {
    const count = usage.get(domain) ?? 0;
    const ok = await confirm(
      count > 0
        ? {
            title: t('settings.dns.confirm.has_services_title'),
            message: t('settings.dns.confirm.has_services_message', { count, domain }),
            confirmLabel: t('common.delete'),
            variant: 'warning',
          }
        : {
            title: t('settings.dns.confirm.delete_title'),
            message: t('settings.dns.confirm.delete_message', { domain }),
            confirmLabel: t('common.delete'),
            variant: 'danger',
          },
    );
    if (ok) deleteDomain.mutate(domain);
  };

  const needle = search.trim().toLowerCase();
  const visible = needle ? domains.filter((d) => d.includes(needle)) : domains;

  return (
    <div className="space-y-6">
      <SettingsSection
        icon={<Globe />}
        title={t('settings.dns.title')}
        description={t('settings.dns.desc')}
        actions={
          domains.length > 0 ? (
            <Badge tone="neutral">{t('settings.dns.count', { count: domains.length })}</Badge>
          ) : undefined
        }
      >
        <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row sm:items-start">
          <Field
            className="flex-1"
            label={t('settings.dns.add_label')}
            error={candidateInvalid ? t('settings.dns.invalid') : undefined}
          >
            <Input
              value={newDomain}
              placeholder={t('settings.dns.placeholder')}
              autoComplete="off"
              spellCheck={false}
              leftIcon={<Globe />}
              onChange={(e) => setNewDomain(e.target.value)}
              className="font-mono"
            />
          </Field>
          <Button
            type="submit"
            className="sm:mt-[1.375rem]"
            leftIcon={<Plus />}
            loading={addDomain.isPending}
            disabled={!candidate || candidateInvalid}
          >
            {t('settings.dns.add')}
          </Button>
        </form>

        {domains.length > SEARCH_THRESHOLD && (
          <SearchInput value={search} onChange={setSearch} placeholder={t('settings.dns.search_placeholder')} />
        )}

        {domainsQuery.isLoading ? (
          <div className="divide-y divide-border rounded-xl border border-border">
            <SkeletonRow columns={3} />
            <SkeletonRow columns={3} />
            <SkeletonRow columns={3} />
          </div>
        ) : domainsQuery.isError ? (
          <InlineAlert
            tone="danger"
            title={t('settings.dns.load_failed')}
            action={
              <Button variant="outline" size="sm" onClick={() => domainsQuery.refetch()}>
                {t('ui.error.retry')}
              </Button>
            }
          >
            {translateApiError(domainsQuery.error, t, t('common.error'))}
          </InlineAlert>
        ) : domains.length === 0 ? (
          <EmptyState compact icon={<Globe />} title={t('settings.dns.empty')} description={t('settings.dns.empty_desc')} />
        ) : visible.length === 0 ? (
          <EmptyState compact title={t('settings.dns.no_match')} />
        ) : (
          <ul className="divide-y divide-border rounded-xl border border-border">
            {visible.map((domain) => {
              const count = usage.get(domain) ?? 0;
              return (
                <li key={domain} className="flex items-center gap-3 px-4 py-3">
                  <span
                    aria-hidden="true"
                    className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
                  >
                    <Globe className="h-4 w-4" />
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-sm text-foreground">{domain}</span>
                  <Badge tone={count > 0 ? 'info' : 'neutral'} className="tabular-nums">
                    {t('settings.dns.service_count', { count })}
                  </Badge>
                  <IconButton
                    label={t('settings.dns.delete_aria', { domain })}
                    icon={<Trash2 />}
                    tooltip
                    disabled={deleteDomain.isPending}
                    onClick={() => void requestDelete(domain)}
                    className="text-muted-foreground hover:text-destructive"
                  />
                </li>
              );
            })}
          </ul>
        )}
      </SettingsSection>
      {ConfirmDialogElement}
    </div>
  );
}
