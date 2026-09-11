import { useCallback, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Cable, Globe, LayoutTemplate, Plug, Server } from 'lucide-react';
import { api } from '@/api/client';
import {
  Button,
  Chip,
  ChipGroup,
  Field,
  InlineAlert,
  Input,
  Modal,
  SectionHeading,
  Select,
  Separator,
  Switch,
  Textarea,
  buttonVariants,
} from '@/components/ui';
import { useProviderTypes } from '@/hooks/useProviderTypes';
import { useUnsavedGuard } from '@/hooks/useUnsavedGuard';
import { useT } from '@/i18n';
import { translateApiError, isHttpStatus } from '@/lib/errors';
import type { Provider, Tag, Template, TemplateIn } from '@/types/api';
import { TemplateIcon } from './TemplateIcon';
import {
  DOMAIN_CUSTOM,
  TEMPLATE_NAME_MAX,
  UNSET,
  emptyTemplateForm,
  splitProviders,
  tagTone,
  toTemplateForm,
  toTemplateIn,
  validateTemplateForm,
  type TemplateFormErrors,
  type TemplateFormState,
} from './types';

export interface TemplateModalProps {
  open: boolean;
  onClose: () => void;
  /** `null` creates; a template edits it in place. */
  template: Template | null;
}

interface ProviderOption {
  value: string;
  label: string;
}

/**
 * The stored id always stays selectable, even when its provider is gone or disabled — a
 * silent reset to "none" would quietly rewrite the template on the next save.
 */
function providerOptions(list: Provider[], current: string, missingLabel: string): ProviderOption[] {
  const options = list.map((p) => ({ value: String(p.id), label: p.name }));
  if (current && !options.some((o) => o.value === current)) options.push({ value: current, label: missingLabel });
  return options;
}

const NO_ERRORS: TemplateFormErrors = {};

/**
 * The form reduced to what it *says*, for comparing against what it was opened on. Tags are
 * sorted first: their order in the array is the order they were clicked in, and a tag turned
 * off and back on would otherwise read as a change nobody made.
 */
const formFingerprint = (state: TemplateFormState): string =>
  JSON.stringify({ ...state, tag_ids: [...state.tag_ids].sort((a, b) => a - b) });

/**
 * The dialog only exists while it is open, and its identity changes with the template it
 * edits. The body below can therefore seed its form from the props it mounts with — no
 * effect has to copy them into state, and every open starts on clean fields.
 */
export function TemplateModal({ open, onClose, template }: TemplateModalProps) {
  if (!open) return null;
  return <TemplateModalBody key={template ? `edit-${template.id}` : 'create'} onClose={onClose} template={template} />;
}

function TemplateModalBody({ onClose, template }: Omit<TemplateModalProps, 'open'>) {
  const t = useT();
  const queryClient = useQueryClient();
  const isEdit = template !== null;

  const [form, setForm] = useState<TemplateFormState>(() =>
    template ? toTemplateForm(template) : emptyTemplateForm,
  );
  const [submitted, setSubmitted] = useState(false);
  const [customDomain, setCustomDomain] = useState(false);
  const [iconBroken, setIconBroken] = useState(false);
  /** The server's 409 on `name`, which no client-side rule can predict. */
  const [nameConflict, setNameConflict] = useState<string | null>(null);

  const patch = useCallback((changes: Partial<TemplateFormState>) => {
    // Typing a new name is what clears "already taken" -- nothing else can.
    if (changes.name !== undefined) setNameConflict(null);
    setForm((prev) => ({ ...prev, ...changes }));
  }, []);

  /**
   * Escape closes the dialog even while `persistent` blocks the backdrop, and the body is
   * unmounted on the way out, which takes the form with it. A template is a dozen decisions --
   * name, scheme, port, mode, three providers, domain, tags -- and one stray key threw the lot
   * away with no undo. Cancel asks the same question, so the two ways out behave alike; a
   * successful save still closes straight through, because the server already has the form.
   */
  const seeded = useMemo(() => (template ? toTemplateForm(template) : emptyTemplateForm), [template]);
  const { requestClose, UnsavedGuardElement } = useUnsavedGuard(
    formFingerprint(form) !== formFingerprint(seeded),
    onClose,
  );

  // --- reference data -----------------------------------------------------
  const { data: providers = [] } = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
  });
  const { data: providerTypes = {} } = useProviderTypes();
  const { data: domains = [] } = useQuery<string[]>({
    queryKey: ['domains'],
    queryFn: () => api.get<string[]>('/domains'),
  });
  const { data: tags = [], isError: tagsError } = useQuery<Tag[]>({
    queryKey: ['tags'],
    queryFn: () => api.get<Tag[]>('/tags'),
  });

  const choices = useMemo(() => splitProviders(providers, providerTypes), [providers, providerTypes]);
  const isTunnel = form.expose_mode === 'tunnel';

  const domainOptions = useMemo(() => {
    const current = form.domain.trim();
    const list = [...domains];
    if (current && !list.includes(current)) list.unshift(current);
    return list;
  }, [domains, form.domain]);

  // --- save ---------------------------------------------------------------
  const save = useMutation<Template, unknown, TemplateIn>({
    mutationFn: (body) =>
      isEdit && template
        ? api.put<Template>(`/templates/${template.id}`, body)
        : api.post<Template>('/templates', body),
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ['templates'] });
      toast.success(
        t(isEdit ? 'templates.toast.updated' : 'templates.toast.created', { name: saved?.name ?? form.name.trim() }),
      );
      onClose();
    },
    onError: (err) => {
      // The name is unique server-side; a 409 belongs on the field, not only in a toast.
      if (isHttpStatus(err, 409)) {
        const message = t('templates.form.name_taken');
        setNameConflict(message);
        setSubmitted(true);
        toast.error(message);
        return;
      }
      toast.error(translateApiError(err, t, t('templates.toast.save_failed')));
    },
  });

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setSubmitted(true);
    if (Object.keys(validateTemplateForm(form, t)).length > 0) return;
    save.mutate(toTemplateIn(form));
  };

  // Errors stay silent until the first submit, then follow every keystroke.
  const errors = useMemo(
    () => (submitted ? validateTemplateForm(form, t) : NO_ERRORS),
    [form, submitted, t],
  );

  const nameError = errors.name ?? nameConflict ?? undefined;
  const errorCount = Object.keys(errors).length + (nameConflict && !errors.name ? 1 : 0);
  const iconPreviewUrl = form.icon_url.trim();

  const renderProviderField = (
    label: string,
    hint: string,
    icon: ReactNode,
    list: Provider[],
    value: string,
    onChange: (value: string) => void,
    emptyMessage: string,
  ) => (
    <Field label={label} hint={hint}>
      {list.length === 0 && !value ? (
        <InlineAlert tone="info" icon={icon}>
          <div className="flex flex-wrap items-center gap-2">
            <span>{emptyMessage}</span>
            <Link to="/providers" className={buttonVariants({ variant: 'link', size: 'sm' })} onClick={onClose}>
              {t('templates.form.add_provider')}
            </Link>
          </div>
        </InlineAlert>
      ) : (
        <Select value={value} onChange={(e) => onChange(e.target.value)}>
          <option value={UNSET}>{t('templates.form.provider_none')}</option>
          {providerOptions(list, value, t('templates.form.provider_missing')).map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      )}
    </Field>
  );

  return (
    <Modal
      open
      onClose={() => void requestClose()}
      size="lg"
      icon={<LayoutTemplate />}
      title={t(isEdit ? 'templates.form.edit_title' : 'templates.form.create_title')}
      description={t(isEdit ? 'templates.form.edit_description' : 'templates.form.create_description')}
      persistent={save.isPending}
      footer={
        <>
          <Button type="button" variant="ghost" onClick={() => void requestClose()} disabled={save.isPending}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" form="template-form" variant="primary" loading={save.isPending}>
            {t(isEdit ? 'templates.form.submit_save' : 'templates.form.submit_create')}
          </Button>
        </>
      }
    >
      <form id="template-form" onSubmit={handleSubmit} className="space-y-6" noValidate>
        {/* --- identity ---------------------------------------------------- */}
        <section className="space-y-4">
          <SectionHeading as="h3" size="sm" title={t('templates.form.section_identity')} />
          <Field
            label={t('templates.form.name')}
            hint={t('templates.form.name_hint')}
            error={nameError}
            required
          >
            <Input
              value={form.name}
              onChange={(e) => patch({ name: e.target.value })}
              placeholder={t('templates.form.name_placeholder')}
              maxLength={TEMPLATE_NAME_MAX}
              autoComplete="off"
            />
          </Field>

          <Field label={t('templates.form.description')}>
            <Textarea
              value={form.description}
              onChange={(e) => patch({ description: e.target.value })}
              placeholder={t('templates.form.description_placeholder')}
              rows={2}
            />
          </Field>

          <Field label={t('templates.form.icon_url')} hint={t('templates.form.icon_hint')} error={errors.icon_url}>
            <div className="flex items-start gap-3">
              <TemplateIcon
                url={iconPreviewUrl}
                size="md"
                onLoadError={() => setIconBroken(true)}
                className="mt-0.5"
              />
              <div className="min-w-0 flex-1 space-y-1.5">
                <Input
                  value={form.icon_url}
                  onChange={(e) => {
                    setIconBroken(false);
                    patch({ icon_url: e.target.value });
                  }}
                  placeholder={t('templates.form.icon_url_placeholder')}
                  autoComplete="off"
                  inputMode="url"
                />
                {iconBroken && iconPreviewUrl && !errors.icon_url && (
                  <p className="text-xs text-warning">{t('templates.form.icon_preview_failed')}</p>
                )}
              </div>
            </div>
          </Field>
        </section>

        <Separator />

        {/* --- target ------------------------------------------------------ */}
        <section className="space-y-4">
          <SectionHeading as="h3" size="sm" title={t('templates.form.section_target')} />
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t('templates.form.forward_scheme')} hint={t('templates.form.scheme_hint')}>
              <Select
                value={form.forward_scheme}
                onChange={(e) => patch({ forward_scheme: e.target.value === 'https' ? 'https' : 'http' })}
              >
                <option value="http">http</option>
                <option value="https">https</option>
              </Select>
            </Field>

            <Field
              label={t('templates.form.target_port')}
              hint={t('templates.form.port_hint')}
              error={errors.target_port}
            >
              <Input
                value={form.target_port}
                onChange={(e) => patch({ target_port: e.target.value.replace(/[^\d]/g, '') })}
                placeholder={t('templates.form.target_port_placeholder')}
                inputMode="numeric"
                autoComplete="off"
              />
            </Field>
          </div>

          <Field inline label={t('templates.form.websocket')} hint={t('templates.form.websocket_hint')}>
            <Switch checked={form.websocket} onCheckedChange={(checked) => patch({ websocket: checked })} />
          </Field>
        </section>

        <Separator />

        {/* --- exposure ---------------------------------------------------- */}
        <section className="space-y-4">
          <SectionHeading as="h3" size="sm" title={t('templates.form.section_exposure')} />

          <Field label={t('templates.form.expose_mode')} hint={t('templates.form.expose_hint')}>
            <Select
              value={form.expose_mode}
              onChange={(e) => patch({ expose_mode: e.target.value === 'tunnel' ? 'tunnel' : 'proxy_dns' })}
            >
              <option value="proxy_dns">{t('templates.form.expose_proxy_dns')}</option>
              <option value="tunnel">{t('templates.form.expose_tunnel')}</option>
            </Select>
          </Field>

          {isTunnel ? (
            renderProviderField(
              t('templates.form.tunnel_provider'),
              t('templates.form.tunnel_provider_hint'),
              <Cable />,
              choices.tunnel,
              form.tunnel_provider_id,
              (value) => patch({ tunnel_provider_id: value }),
              t('templates.form.no_tunnel_providers'),
            )
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {renderProviderField(
                t('templates.form.proxy_provider'),
                t('templates.form.proxy_provider_hint'),
                <Server />,
                choices.proxy,
                form.proxy_provider_id,
                (value) => patch({ proxy_provider_id: value }),
                t('templates.form.no_proxy_providers'),
              )}
              {renderProviderField(
                t('templates.form.dns_provider'),
                t('templates.form.dns_provider_hint'),
                <Globe />,
                choices.dns,
                form.dns_provider_id,
                (value) => patch({ dns_provider_id: value }),
                t('templates.form.no_dns_providers'),
              )}
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t('templates.form.domain')} hint={t('templates.form.domain_hint')}>
              <Select
                value={customDomain ? DOMAIN_CUSTOM : form.domain}
                onChange={(e) => {
                  const value = e.target.value;
                  if (value === DOMAIN_CUSTOM) {
                    setCustomDomain(true);
                    return;
                  }
                  setCustomDomain(false);
                  patch({ domain: value });
                }}
              >
                <option value="">{t('templates.form.domain_none')}</option>
                {domainOptions.map((domain) => (
                  <option key={domain} value={domain}>
                    {domain}
                  </option>
                ))}
                <option value={DOMAIN_CUSTOM}>{t('templates.form.domain_custom')}</option>
              </Select>
            </Field>

            {!isTunnel && (
              <Field label={t('templates.form.dns_ip')} hint={t('templates.form.dns_ip_hint')} error={errors.dns_ip}>
                <Input
                  value={form.dns_ip}
                  onChange={(e) => patch({ dns_ip: e.target.value })}
                  placeholder={t('templates.form.dns_ip_placeholder')}
                  autoComplete="off"
                />
              </Field>
            )}
          </div>

          {customDomain && (
            <Field
              label={t('templates.form.domain_custom_label')}
              hint={t('templates.form.domain_custom_hint')}
              error={errors.domain}
            >
              <Input
                value={form.domain}
                onChange={(e) => patch({ domain: e.target.value.trim().toLowerCase() })}
                placeholder={t('templates.form.domain_custom_placeholder')}
                autoComplete="off"
              />
            </Field>
          )}

          {!isTunnel && (
            <Field label={t('templates.form.public_target_mode')} hint={t('templates.form.public_target_hint')}>
              <Select
                value={form.public_target_mode}
                onChange={(e) => patch({ public_target_mode: e.target.value === 'auto' ? 'auto' : 'manual' })}
              >
                <option value="manual">{t('templates.form.target_manual')}</option>
                <option value="auto">{t('templates.form.target_auto')}</option>
              </Select>
            </Field>
          )}
        </section>

        <Separator />

        {/* --- tags -------------------------------------------------------- */}
        <section className="space-y-3">
          <SectionHeading
            as="h3"
            size="sm"
            title={t('templates.form.tags')}
            description={t('templates.form.tags_hint')}
          />
          {tags.length === 0 && tagsError ? (
            // Not an invitation to go and create one: there may well be plenty, and this
            // list simply did not load.
            <InlineAlert tone="danger">{t('ui.error.list_unavailable')}</InlineAlert>
          ) : tags.length === 0 ? (
            <InlineAlert tone="info" icon={<Plug />}>
              <div className="flex flex-wrap items-center gap-2">
                <span>{t('templates.form.no_tags')}</span>
                <Link
                  to="/settings?tab=taxonomy"
                  className={buttonVariants({ variant: 'link', size: 'sm' })}
                  onClick={onClose}
                >
                  {t('templates.form.manage_tags')}
                </Link>
              </div>
            </InlineAlert>
          ) : (
            <ChipGroup label={t('templates.form.tags')}>
              {tags.map((tag) => {
                const selected = form.tag_ids.includes(tag.id);
                return (
                  <Chip
                    key={tag.id}
                    size="sm"
                    tone={tagTone(tag.color)}
                    selected={selected}
                    onClick={() =>
                      patch({
                        tag_ids: selected ? form.tag_ids.filter((id) => id !== tag.id) : [...form.tag_ids, tag.id],
                      })
                    }
                  >
                    {tag.name}
                  </Chip>
                );
              })}
            </ChipGroup>
          )}
        </section>

        {submitted && errorCount > 0 && <InlineAlert tone="danger">{t('templates.form.fix_errors')}</InlineAlert>}
      </form>
      {UnsavedGuardElement}
    </Modal>
  );
}
