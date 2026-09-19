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
import { fqdnOf } from '@/components/features/services/helpers';
import type { Service, Template } from '@/types/api';
import { DomainDeleteBody, type DomainDependent } from './DomainDeleteBody';
import { SettingsSection } from './SettingsSection';

// `POST /api/domains` runs `is_valid_domain(..., require_dot=True)` (`app/api/settings.py`),
// so a single label (`lan`, `home`) is refused by the API: at least one dot, or the form
// would accept what the request then rejects.
const DOMAIN_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/;
const SEARCH_THRESHOLD = 6;

/** What a root domain still holds: rows that keep working after it is deleted. */
interface Dependents {
  services: DomainDependent[];
  templates: DomainDependent[];
}

/** One shared empty object rather than a fresh `{ services: [], templates: [] }` per row per render. */
const NO_DEPENDENTS: Dependents = { services: [], templates: [] };

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
  const servicesQuery = useQuery<Service[]>({
    queryKey: ['services'],
    queryFn: () => api.get<Service[]>('/services'),
  });
  // Templates name a root domain in exactly the same bare TEXT column services do, and this
  // tab used to read only the services. A domain no service used but a template did showed
  // the neutral "0 services" badge and the plain "Delete domain?" question.
  const templatesQuery = useQuery<Template[]>({
    queryKey: ['templates'],
    queryFn: () => api.get<Template[]>('/templates'),
  });

  const domains = useMemo(() => domainsQuery.data ?? [], [domainsQuery.data]);
  const services = useMemo(() => servicesQuery.data ?? [], [servicesQuery.data]);
  const templates = useMemo(() => templatesQuery.data ?? [], [templatesQuery.data]);

  // Those two reads were `data = []` with nothing destructured to notice a failure, beside a
  // `domainsQuery` that has a skeleton, an error with a retry and an empty state. So a
  // `/services` that failed, or had simply not landed yet, painted "0 services" on every row
  // -- a stated count, not a blank -- and sent `requestDelete` down the branch written for a
  // domain nothing is built on. Nothing refuses that deletion on the way through:
  // `DELETE /api/domains` looks the holders up only to journal them. This dialog is the whole
  // guard, and what decided which one to ask was the emptiness of a read nobody checked.
  const usageUnknown =
    servicesQuery.isPending ||
    servicesQuery.isError ||
    templatesQuery.isPending ||
    templatesQuery.isError;
  const usageFailed = servicesQuery.isError || templatesQuery.isError;
  const usageError = servicesQuery.error ?? templatesQuery.error;
  const dependents = useMemo(() => {
    const byDomain = new Map<string, Dependents>();
    const slot = (name: string) => {
      let entry = byDomain.get(name);
      if (!entry) byDomain.set(name, (entry = { services: [], templates: [] }));
      return entry;
    };
    for (const service of services) {
      slot(service.domain).services.push({ id: service.id, label: fqdnOf(service) });
    }
    for (const template of templates) {
      // A template may leave the domain to the service it creates. That blank names no root,
      // and this map is keyed by one: the empty key is read only when `domains` itself holds
      // an empty name, which `app/api/sync.py` can write -- `fqdn.split(".", 1)` over a single
      // label with a trailing dot yields `("host", "")`. The badge that row would then paint
      // counts templates against a domain they are not built on.
      if (template.domain) slot(template.domain).templates.push({ id: template.id, label: template.name });
    }
    for (const entry of byDomain.values()) {
      entry.services.sort((a, b) => a.label.localeCompare(b.label));
      entry.templates.sort((a, b) => a.label.localeCompare(b.label));
    }
    return byDomain;
  }, [services, templates]);

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
    const held = dependents.get(domain) ?? NO_DEPENDENTS;
    const inUse = held.services.length + held.templates.length > 0;
    const ok = await confirm(
      usageUnknown
        ? {
            // Not "nothing is built on it": nobody knows. The operator keeps the deletion --
            // withholding it would strand the tab on a read it may never get -- and is told
            // the list under it is missing rather than empty.
            title: t('settings.dns.confirm.unknown_title'),
            message: t('settings.dns.confirm.unknown_message', { domain }),
            confirmLabel: t('common.delete'),
            variant: 'danger',
          }
        : inUse
        ? {
            title: t('settings.dns.confirm.in_use_title'),
            message: <DomainDeleteBody domain={domain} services={held.services} templates={held.templates} />,
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
            className="sm:mt-5.5"
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

        {usageFailed && (
          <InlineAlert
            tone="warning"
            title={t('settings.dns.usage_failed')}
            action={
              <Button
                variant="outline"
                size="sm"
                loading={servicesQuery.isFetching || templatesQuery.isFetching}
                onClick={() => {
                  void servicesQuery.refetch();
                  void templatesQuery.refetch();
                }}
              >
                {t('ui.error.retry')}
              </Button>
            }
          >
            {translateApiError(usageError, t, t('common.error'))}
          </InlineAlert>
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
              const held = dependents.get(domain) ?? NO_DEPENDENTS;
              return (
                <li key={domain} className="flex items-center gap-3 px-4 py-3">
                  <span
                    aria-hidden="true"
                    className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
                  >
                    <Globe className="h-4 w-4" />
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-sm text-foreground">{domain}</span>
                  {usageUnknown ? (
                    <Badge tone="warning">{t('settings.dns.usage_unknown')}</Badge>
                  ) : (
                    <>
                      <Badge tone={held.services.length > 0 ? 'info' : 'neutral'} className="tabular-nums">
                        {t('settings.dns.service_count', { count: held.services.length })}
                      </Badge>
                      {held.templates.length > 0 && (
                        <Badge tone="info" className="tabular-nums">
                          {t('settings.dns.template_count', { count: held.templates.length })}
                        </Badge>
                      )}
                    </>
                  )}
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
