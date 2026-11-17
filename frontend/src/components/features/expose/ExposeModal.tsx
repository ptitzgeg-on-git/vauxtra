import { type FormEvent, type ReactNode, useEffect, useId, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowRight,
  BookmarkPlus,
  Check,
  CircleAlert,
  CircleCheck,
  FlaskConical,
  Network,
  RefreshCw,
  TriangleAlert,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { useProviderTypes } from '@/hooks/useProviderTypes';
import { cn } from '@/lib/cn';
import { getHttpStatus, translateApiError, isHttpStatus } from '@/lib/errors';
import {
  domainProblem,
  domainProblemKey,
  fqdnProblem,
  fqdnProblemKey,
  subdomainProblem,
  subdomainProblemKey,
} from '@/lib/hostname';
import {
  Badge,
  Button,
  InlineAlert,
  Input,
  Modal,
  ProviderLogo,
  SectionHeading,
  type Tone,
  useConfirmDialog,
} from '@/components/ui';
import type {
  AccessProtectBody,
  AccessProtectResult,
  DryRunPlan,
  Environment,
  PreflightCheck,
  PreflightRequest,
  PreflightResult,
  PushPlanDnsAction,
  PushPlanProxyAction,
  Service,
  ServiceLabelsPayload,
  ServicePayload,
  Tag,
  Template,
  TemplateIn,
  TunnelAccessResponse,
} from '@/types/api';
import {
  type FieldErrors,
  type FormState,
  type Provider,
  autoPublicTarget,
  fqdnOf,
  initialForm,
  providerHasCapability,
  publicTargetSourceLabel,
  preflightDetailText,
  toFormState,
} from './types';
import { parseTarget, proxyHostTarget, withTargetSplit } from './target';
import { TUNNEL_ACCESS_KEY } from '@/components/features/monitoring/useTunnelAccess';
import { ServiceForm } from './ServiceForm';
import { ServicePreview } from './ServicePreview';

interface ExposeModalProps {
  isOpen: boolean;
  onClose: () => void;
  mode?: 'create' | 'edit';
  service?: Service | null;
  /** Seed for a create form (a template applied through `?template=<id>`); ignored in edit mode. */
  initialState?: FormState | null;
  /** Name of the template the seed came from, shown under the title. */
  templateName?: string | null;
}

interface TargetSuggestion {
  candidates: Array<{ value: string; source: string }>;
  recommended: string;
}

type Step = 'configure' | 'review' | 'done';
const STEPS: Step[] = ['configure', 'review', 'done'];

/**
 * Configure → Review → Done, the current one announced with `aria-current`.
 *
 * A step in `skipped` is passed without a tick. An edit that changes labels and nothing else
 * goes from Configure straight to Done, and a ticked Review would say the checks ran.
 */
function Stepper({
  current,
  label,
  labels,
  skipped = [],
}: {
  current: Step;
  label: string;
  labels: Record<Step, string>;
  skipped?: Step[];
}) {
  const currentIndex = STEPS.indexOf(current);
  return (
    <ol aria-label={label} className="flex items-center gap-2 text-xs font-medium">
      {STEPS.map((step, index) => {
        const done = index < currentIndex && !skipped.includes(step);
        const active = index === currentIndex;
        return (
          <li key={step} className="flex items-center gap-2">
            <span
              aria-current={active ? 'step' : undefined}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 transition-colors',
                active && 'border-primary bg-primary/10 text-primary',
                done && 'border-success/30 bg-success/10 text-success',
                !active && !done && 'border-border bg-muted text-muted-foreground',
              )}
            >
              <span
                aria-hidden="true"
                className={cn(
                  'inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-semibold',
                  active && 'bg-primary text-primary-foreground',
                  done && 'bg-success text-success-foreground',
                  !active && !done && 'bg-border text-muted-foreground',
                )}
              >
                {done ? <Check className="h-2.5 w-2.5" /> : index + 1}
              </span>
              {labels[step]}
            </span>
            {index < STEPS.length - 1 && <span aria-hidden="true" className="h-px w-6 bg-border" />}
          </li>
        );
      })}
    </ol>
  );
}

type CheckTone = 'success' | 'warning' | 'danger';
const checkTone = (check: PreflightCheck): CheckTone => (check.ok ? 'success' : check.blocking ? 'danger' : 'warning');
const CHECK_ICONS: Record<CheckTone, ReactNode> = {
  success: <CircleCheck className="h-4 w-4 text-success" />,
  warning: <TriangleAlert className="h-4 w-4 text-warning" />,
  danger: <CircleAlert className="h-4 w-4 text-destructive" />,
};

// A withdrawal is not the same kind of write as a publication, and reading the badge colour
// as "this is routine" would be the wrong reading on a plan that removes routes. Anything the
// map does not name falls back to `info`, so a new action word from the API is rendered rather
// than swallowed.
const PLAN_ACTION_TONE: Partial<Record<PushPlanProxyAction['action'] | PushPlanDnsAction['action'], Tone>> = {
  skip_read_only: 'neutral',
  suspend: 'warning',
  delete: 'warning',
  // A resume is a repair rather than a routine write: the route is down right now, which is
  // the same thing the drift report says one panel away.
  resume: 'warning',
};

/** The finished sentence, with every mention of the hostname set in mono.
 *
 * `t` returns a plain string, so a sentence that names the host cannot carry markup of its
 * own and the emphasis has to be put back here. Splitting on the host rather than writing
 * it out after the sentence is what lets each locale keep it where its grammar wants it:
 * English and French open on the hostname, and the sentence used to be rendered with the
 * placeholder still in it because nothing filled it.
 *
 * Exported for the test beside it, the way `Button.tsx` exports `buttonVariants`: the
 * splitting is what the test is about, and the test providers leave `I18nProvider` out, so
 * driving the wizard to its `done` step would hand this the key `expose.done.body` and there
 * would be nothing in it to split on.
 */
// eslint-disable-next-line react-refresh/only-export-components -- a plain string helper, not a second component; the same exception Button.tsx takes for buttonVariants
export function withHostHighlighted(sentence: string, host: string): ReactNode[] {
  return sentence.split(host).flatMap((part, index) =>
    index === 0
      ? [part]
      : [
          <span key={`host-${index}`} className="font-mono text-foreground">
            {host}
          </span>,
          part,
        ],
  );
}

/**
 * What became of the Access policies picked for a tunnel route: `null` when none were, a count
 * when the application was created, and the sentence to show when it was not.
 */
type AccessOutcome = { ok: true; count: number } | { ok: false; message: string } | null;

const isRecordWithErrors = (value: unknown): value is { errors: string[] } =>
  Boolean(value) && typeof value === 'object' && Array.isArray((value as { errors?: unknown }).errors);

/** The name a route answers on: its tunnel hostname when it has one, else subdomain and domain. */
const publicHostOf = (route: Pick<ServicePayload, 'expose_mode' | 'tunnel_hostname' | 'subdomain' | 'domain'>): string =>
  route.expose_mode === 'tunnel' && route.tunnel_hostname
    ? route.tunnel_hostname
    : `${route.subdomain}.${route.domain}`;

/** A set of ids, written so that the order they were ticked in does not count. */
const idSet = (ids: number[]) => JSON.stringify([...new Set(ids)].sort((a, b) => a - b));

/**
 * What an edit changed, when all it changed is labels; null otherwise.
 *
 * Tags, environments and the icon are Vauxtra's own metadata: no proxy host, DNS record or
 * tunnel rule carries them. The wizard sent them through the checks and a `PUT`, which
 * publishes the route again on its proxy or tunnel, so a tag cost a round of provider calls
 * and, on a tunnel, a rewrite of its rule. `PATCH` writes the three columns and calls no
 * provider. Only the keys that moved are sent, because the journal names what a save
 * changed.
 *
 * Null as soon as anything else on the form moved, and null on an untouched form too, which
 * keeps going through the checks and a `PUT`: saving an unchanged route is how an operator
 * publishes it again. Ids are compared as sets, so unticking a tag and ticking it again,
 * which only moves it to the end of the list, is not a change.
 */
function labelChanges(form: FormState, saved: FormState): ServiceLabelsPayload | null {
  const routeOf = (f: FormState) => JSON.stringify({ ...f, tag_ids: null, environment_ids: null, icon_url: null });
  if (routeOf(form) !== routeOf(saved)) return null;
  const changes: ServiceLabelsPayload = {};
  if (idSet(form.tag_ids) !== idSet(saved.tag_ids)) changes.tag_ids = form.tag_ids;
  if (idSet(form.environment_ids) !== idSet(saved.environment_ids)) changes.environment_ids = form.environment_ids;
  if (form.icon_url !== saved.icon_url) changes.icon_url = form.icon_url;
  return Object.keys(changes).length > 0 ? changes : null;
}

export function ExposeModal({
  isOpen,
  onClose,
  mode = 'create',
  service = null,
  initialState = null,
  templateName = null,
}: ExposeModalProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const isEditMode = mode === 'edit' && Boolean(service?.id);
  const serviceId = isEditMode ? Number(service?.id) : null;

  const seedForm = (): FormState => (isEditMode ? toFormState(service) : (initialState ?? initialForm));

  const [formData, setFormData] = useState<FormState>(seedForm);
  const [step, setStep] = useState<Step>('configure');
  const [formError, setFormError] = useState<string | null>(null);
  const formErrorRef = useRef<HTMLDivElement | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);
  // Set by the first refused "Continue", and kept until the form closes: from then on each
  // field says what is wrong with it, and stops saying it as soon as it is put right.
  const [attempted, setAttempted] = useState(false);
  // A refused save, said on the review step it leaves the operator on.
  const [saveError, setSaveError] = useState<string | null>(null);
  const saveErrorRef = useRef<HTMLDivElement | null>(null);
  // The opening of the dialog now on screen, null while it is closed. Every request the dialog
  // makes carries the opening it was started in, and its answer touches the dialog only while
  // that same opening is on screen: closed since, even if opened again, the form on screen is
  // not the one that asked. A read whose opening is gone (the checks, the dry run, the lookup
  // "Detect" waits for) is dropped, since nothing changed on the server. A write whose opening
  // is gone (the save, the labels, the template) is said in a toast alone (ux-audit.md § 3.7),
  // and sets nothing in the dialog. Caches are invalidated either way: the server changed
  // whoever is looking.
  const openingRef = useRef<object | null>(null);
  useEffect(() => {
    if (!isOpen) return;
    openingRef.current = {};
    return () => {
      openingRef.current = null;
    };
  }, [isOpen]);
  // A ref, not state: an answer read by a callback from an older render still sees the
  // opening on screen now.
  const isOnScreen = (opening: object | null) => opening !== null && opening === openingRef.current;

  // The banner sits at the top of a body that scrolls, and "Continue" sits at the bottom of
  // it, so the answer to a click could land entirely off screen. Bring it back into view.
  useEffect(() => {
    if (formError) formErrorRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [formError]);
  // The same for a refused save, which only ever said so in a toast (ux-audit.md § 1.8).
  useEffect(() => {
    if (saveError) saveErrorRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [saveError]);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  // What the checks were asked about, and so what "Create" acts on: the body it sends and the
  // Access policies it applies once the route exists, frozen together. Read from the form at
  // "Create" instead, they could differ from what the review showed: lists fetched again since
  // rebuild another body, and a late "Detect" answer still writes the DNS target. The pair is
  // taken at the click, on "Continue" or on "Check again", with the body sent to the checks:
  // what is typed while they run is not in it. Going back to the form drops it, "Check again"
  // replaces it, and without one "Create" is off: a review with no reviewed body publishes
  // nothing.
  const [reviewed, setReviewed] = useState<{ payload: ServicePayload; accessPolicyIds: string[] } | null>(null);
  const [dryRun, setDryRun] = useState<DryRunPlan | null>(null);
  // `labelsOnly`: the save went through `PATCH`, and the last step says no provider was asked.
  // `access`: the Access application asked for a tunnel route, see `protectRoute`.
  const [saveOutcome, setSaveOutcome] = useState<{
    host: string;
    errors: string[];
    labelsOnly?: boolean;
    access?: AccessOutcome;
  } | null>(null);
  const [templateDraftName, setTemplateDraftName] = useState('');
  const { confirm, ConfirmDialogElement } = useConfirmDialog();

  // Whole, like the three reads under it. This is the list the proxy, the DNS provider and
  // the tunnel are each picked from, and the form is what writes the route: a read that
  // failed arrived here as an empty array and the section below said, in its own words,
  // that this instance has no proxy at all.
  const {
    data: providers = [],
    isLoading: isLoadingProviders,
    isError: providersError,
    isFetching: isFetchingProviders,
    refetch: refetchProviders,
  } = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
    enabled: isOpen,
    // The form's links to set up what it lacks open in another tab, and what was added there
    // has to be here on the way back (ux-audit.md § 1.7). The app turns this off for every
    // other read; with its staleTime of 0, `true` reads again on each return to the window.
    refetchOnWindowFocus: true,
  });

  const { data: domains = [], isLoading: isLoadingDomains, isError: domainsError } = useQuery<string[]>({
    queryKey: ['domains'],
    queryFn: () => api.get<string[]>('/domains'),
    enabled: isOpen,
    // As for the integrations above: a domain added in another tab is offered on return.
    refetchOnWindowFocus: true,
  });

  const { data: providerTypes = {} } = useProviderTypes({ enabled: isOpen });

  // Their setup links open another tab too (`SetupLink`), so they are read again on return.
  const { data: tags = [], isLoading: isLoadingTags, isError: tagsError } = useQuery<Tag[]>({
    queryKey: ['tags'],
    queryFn: () => api.get<Tag[]>('/tags'),
    enabled: isOpen,
    refetchOnWindowFocus: true,
  });

  const {
    data: environments = [],
    isLoading: isLoadingEnvironments,
    isError: environmentsError,
  } = useQuery<Environment[]>({
    queryKey: ['environments'],
    queryFn: () => api.get<Environment[]>('/environments'),
    enabled: isOpen,
    refetchOnWindowFocus: true,
  });

  const providerTypeMap = providerTypes;

  // Derived provider lists for the preview and the payload rules.
  const allProviders = providers.filter((p) => Boolean(p.enabled));
  const proxyProviders = allProviders.filter((p) => providerHasCapability(p, 'proxy', providerTypeMap));
  const dnsProviders = allProviders.filter((p) => providerHasCapability(p, 'dns', providerTypeMap));
  const tunnelProviders = proxyProviders.filter((p) => providerHasCapability(p, 'supports_tunnel', providerTypeMap));

  const selectedProxy = proxyProviders.find((p) => String(p.id) === formData.proxy_provider_id);
  const selectedDns = dnsProviders.find((p) => String(p.id) === formData.dns_provider_id);
  const selectedTunnel = tunnelProviders.find((p) => String(p.id) === formData.tunnel_provider_id);
  const selectedExtraProxies = useMemo(
    () => proxyProviders.filter((p) => formData.extra_proxy_provider_ids.includes(String(p.id))),
    [proxyProviders, formData.extra_proxy_provider_ids],
  );
  const selectedExtraDns = useMemo(
    () => dnsProviders.filter((p) => formData.extra_dns_provider_ids.includes(String(p.id))),
    [dnsProviders, formData.extra_dns_provider_ids],
  );

  // Public target suggestion
  const suggestionUrl = formData.proxy_provider_id
    ? `/services/public-target/suggest?proxy_provider_id=${encodeURIComponent(formData.proxy_provider_id)}`
    : '/services/public-target/suggest';

  const selectedDnsProvider = selectedDns;
  const selectedDnsSupportsAuto = selectedDnsProvider
    ? providerHasCapability(selectedDnsProvider, 'supports_auto_public_target', providerTypeMap)
    : false;
  const selectedDnsIsExternal = selectedDnsProvider
    ? providerHasCapability(selectedDnsProvider, 'public_dns', providerTypeMap)
    : false;
  const selectedDnsIsLocal = Boolean(selectedDnsProvider) && !selectedDnsIsExternal;

  // Where a LAN record left empty points: at the proxy the route goes through (ux-audit.md
  // § 1.2), or in DNS alone at the service itself. Worked out from the form it is given, so
  // the hint, `validate` and the payload give one answer, and a change of proxy carries the
  // record along. It used to be copied into the field by the proxy select alone, and only when
  // the resolver had been picked first, which the form's own order makes the rare case.
  const proxyDnsTarget = proxyHostTarget(selectedProxy?.url);
  const localDnsFallbackOf = (form: FormState): string =>
    form.expose_mode !== 'proxy_dns' || !selectedDnsIsLocal
      ? ''
      : form.ui_expose_mode === 'dns_only'
        ? form.target_ip.trim()
        : proxyDnsTarget;

  const shouldSuggestTarget =
    isOpen &&
    formData.expose_mode === 'proxy_dns' &&
    formData.public_target_mode === 'auto' &&
    Boolean(formData.dns_provider_id) &&
    selectedDnsSupportsAuto;

  // `detect_server_public_ip` makes a real outbound call, so this read is both slow and
  // failable. Without `isError` a lookup that never answered arrived as the same empty
  // string as a proxy that genuinely has no public target, and the form then said the
  // second out loud.
  const {
    data: targetSuggestion,
    isFetching: isFetchingTargetSuggestion,
    isError: targetSuggestionError,
    refetch: refetchTargetSuggestion,
  } = useQuery<TargetSuggestion>({
    queryKey: ['public-target-suggest', formData.proxy_provider_id],
    queryFn: () => api.get<TargetSuggestion>(suggestionUrl),
    enabled: shouldSuggestTarget,
    staleTime: 45_000,
  });
  // "Detect" waits for this lookup, and the opening is taken at its click, before the answer
  // is awaited: an answer whose opening is gone resolves to nothing, so it writes no target
  // into the form opened since. In the same opening, the guard in `ServiceForm` drops an
  // answer once the target, the DNS or the proxy has changed since the click.
  const refetchTargetForThisOpening = async () => {
    const opening = openingRef.current;
    const result = await refetchTargetSuggestion();
    return isOnScreen(opening) ? result : undefined;
  };

  // The lookup's answer is for a public zone, and only there does the form show it. A disabled
  // query still hands back what it last read, so a lookup made a moment before for a public
  // zone, or in a form since discarded, stood in for the LAN record the hint announces.
  const suggestedDnsTarget = selectedDnsIsExternal ? String(targetSuggestion?.recommended || '').trim() : '';

  const fqdnPreview = fqdnOf(formData) ?? t('expose.preview.host_placeholder');

  // What the payload sends and what the form draws are the same call, so the two cannot
  // answer differently for a provider the catalogue has not resolved.
  const publicTarget = autoPublicTarget(formData, selectedDnsProvider, providerTypeMap);

  /**
   * Every payload rule lives here: what the API receives on create, edit and preflight.
   *
   * `form` is the form on screen, or the one "Continue" is about to put there, which differs
   * from it by a split target alone. The name, the public target and the providers are read
   * from the screen's.
   */
  const buildPayload = (form: FormState = formData): ServicePayload => {
    const tunnelHostname = (form.tunnel_hostname.trim() || fqdnPreview).toLowerCase();
    const manualDnsTarget = form.dns_ip.trim();

    return {
      subdomain: form.subdomain.trim().toLowerCase(),
      domain: form.domain.trim().toLowerCase(),
      target_ip: form.target_ip.trim(),
      target_port: Number(form.target_port),
      forward_scheme: form.forward_scheme,
      websocket: form.websocket,
      expose_mode: form.expose_mode,
      public_target_mode: form.expose_mode === 'proxy_dns' ? publicTarget.mode : 'manual',
      auto_update_dns: form.expose_mode === 'proxy_dns' ? publicTarget.autoUpdateDns : false,
      tunnel_provider_id:
        form.expose_mode === 'tunnel' && form.tunnel_provider_id ? Number(form.tunnel_provider_id) : null,
      tunnel_hostname: form.expose_mode === 'tunnel' ? tunnelHostname : '',
      enabled: isEditMode ? Boolean(service?.enabled ?? true) : true,
      proxy_provider_id:
        form.expose_mode === 'proxy_dns' && form.proxy_provider_id ? Number(form.proxy_provider_id) : null,
      dns_provider_id:
        form.expose_mode === 'proxy_dns' && form.dns_provider_id ? Number(form.dns_provider_id) : null,
      dns_ip: form.expose_mode === 'proxy_dns' ? manualDnsTarget || suggestedDnsTarget || localDnsFallbackOf(form) : '',
      tag_ids: form.tag_ids,
      environment_ids: form.environment_ids,
      icon_url: form.icon_url,
      extra_proxy_provider_ids: form.extra_proxy_provider_ids
        .filter(
          (id) => id !== (form.expose_mode === 'tunnel' ? form.tunnel_provider_id : form.proxy_provider_id),
        )
        .map((id) => Number(id)),
      extra_dns_provider_ids:
        form.expose_mode === 'proxy_dns'
          ? form.extra_dns_provider_ids.filter((id) => id !== form.dns_provider_id).map((id) => Number(id))
          : [],
    };
  };

  /**
   * Everything wrong with the form, one translated sentence per field, keyed by the field it
   * is drawn under; empty when the form can be sent. It used to stop at the first problem, so
   * a form with three gaps took three refusals to fill, each said away from its field
   * (ux-audit.md § 1.6). Keys go in in the order the fields are drawn, so the first one is
   * the first field an operator meets.
   */
  const validate = (form: FormState = formData): FieldErrors => {
    const problems: FieldErrors = {};
    // The same rules as `app/validators.py`. Without them "Continue" sent the name anyway and
    // came back with a 422 the panel could only describe as "the checks could not run". An
    // empty half is one of their answers, so a missing name is said by the same rules.
    const badSubdomain = subdomainProblem(form.subdomain, { allowWildcard: true });
    if (badSubdomain) problems.subdomain = t(subdomainProblemKey(badSubdomain));
    const badDomain = domainProblem(form.domain);
    if (badDomain) problems.domain = t(domainProblemKey(badDomain));
    // Only once both halves are sound: two legal halves can still make a name no zone will
    // carry. Under the subdomain, where the field draws the same rule.
    const badFqdn = !badSubdomain && !badDomain ? fqdnProblem(form.subdomain, form.domain) : null;
    if (badFqdn) problems.subdomain = t(fqdnProblemKey(badFqdn));

    // A local resolver in DNS alone publishes the service's own LAN address (see
    // `localDnsFallbackOf`), so there the target is the record as well as the destination.
    if (!form.target_ip.trim()) {
      problems.target_ip =
        form.ui_expose_mode === 'dns_only'
          ? t('expose.validation.target_required_dns_only')
          : t('expose.validation.target_required');
    }

    // `validate_port_when_forwarded` in `app/api/services.py`: 0 is only for a name published
    // in DNS alone, and a proxy host or a tunnel rule pointed at it is a route to nowhere.
    const forwarded =
      form.expose_mode === 'tunnel' ||
      Boolean(form.proxy_provider_id) ||
      form.extra_proxy_provider_ids.length > 0;
    if (!form.target_port && forwarded) problems.target_port = t('expose.validation.port_required');

    if (form.expose_mode === 'tunnel' && !form.tunnel_provider_id) {
      problems.tunnel_provider = t('expose.validation.tunnel_provider_required');
    }
    if (
      form.expose_mode === 'proxy_dns' &&
      !form.proxy_provider_id &&
      !form.dns_provider_id &&
      form.extra_proxy_provider_ids.length === 0 &&
      form.extra_dns_provider_ids.length === 0
    ) {
      problems.providers =
        form.ui_expose_mode === 'dns_only'
          ? t('expose.validation.dns_required_dns_only')
          : t('expose.validation.provider_required');
    }

    // The same three answers as the payload, in the same order, so what passes here is what
    // is sent: `suggestedDnsTarget` is empty unless the zone is public.
    const manualDnsTarget = form.dns_ip.trim();

    if (form.expose_mode === 'proxy_dns' && form.dns_provider_id) {
      if (publicTarget.mode === 'manual' && !manualDnsTarget) {
        if (form.ui_expose_mode === 'dns_only') {
          // A local resolver may answer with the service's own LAN address, and that is what
          // `localDnsFallbackOf` publishes. A public zone must not carry one, so `target_ip`
          // gives it nothing: without a target of its own there is simply nothing to write.
          if (selectedDnsIsExternal && !suggestedDnsTarget) {
            problems.dns_ip = t('expose.validation.dns_target_external_required');
          }
        } else if (selectedDnsIsExternal) {
          problems.dns_ip = t('expose.validation.dns_target_external_required');
        } else if (selectedDnsIsLocal && !localDnsFallbackOf(form)) {
          // Left empty, the record points at the proxy; only a route whose proxy has no
          // address to offer leaves it nothing to point at.
          problems.dns_ip = t('expose.validation.dns_target_local_required');
        }
      }
      if (publicTarget.mode === 'auto' && !manualDnsTarget && !suggestedDnsTarget) {
        if (!selectedDnsProvider && providersError) {
          // The provider list could not be read, so it says nothing about the route's DNS
          // provider. On the failed read, not on an empty list: rows an earlier read left in
          // the cache do not prove the provider is missing either.
          problems.dns_ip = t('expose.validation.auto_target_dns_unread');
        } else if (!selectedDnsProvider) {
          // A fact about the DNS provider, not the proxy, so it is said before the three below:
          // the route's DNS provider is gone from the list, or of a type this build does not
          // know. There is then no public zone to fill, and even a lookup that answered would
          // not be used (`suggestedDnsTarget` reads it for a public zone only).
          problems.dns_ip = t('expose.validation.auto_target_dns_unknown');
        } else {
          // Three states reach here as the same empty string: a lookup that failed, one still
          // in flight, and one that answered with nothing. Only the last is a fact about the
          // proxy -- the other two are facts about a question that has no answer yet, and
          // saying "this proxy cannot" of them sends the operator to check the wrong thing.
          problems.dns_ip = targetSuggestionError
            ? t('expose.validation.auto_target_unread')
            : isFetchingTargetSuggestion
              ? t('expose.validation.auto_target_pending')
              : t('expose.validation.no_auto_target');
        }
      }
    }
    return problems;
  };

  // Drawn under the fields once "Continue" has been refused, and worked out again on each
  // render from then on, so a field that is put right stops saying it is wrong.
  const fieldErrors: FieldErrors = attempted ? validate() : {};

  const runPreflight = useMutation({
    mutationFn: ({ payload }: { payload: ServicePayload; accessPolicyIds: string[]; opening: object | null }) => {
      const body: PreflightRequest = { ...payload, service_id: serviceId };
      return api.post<PreflightResult>('/services/preflight', body);
    },
    onSuccess: (result, { payload, accessPolicyIds, opening }) => {
      // Checks asked by a dialog closed since are dropped: shown in the form opened after it,
      // they put a route that form never asked about one click away from being published.
      if (!isOnScreen(opening)) return;
      setPreflight(result);
      setReviewed({ payload, accessPolicyIds });
      setStep('review');
    },
    onError: (err: unknown, { opening }) => {
      // Dropped too: nothing changed on the server, and a toast over a new form would speak of
      // checks it never asked for.
      if (!isOnScreen(opening)) return;
      // A 422 is the body being refused, not the checks failing to run, and saying the second
      // about the first sends an operator looking at their providers over a typed character.
      const fallback = isHttpStatus(err, 422) ? t('expose.preflight.rejected') : t('expose.preflight.failed');
      const message = translateApiError(err, t, fallback);
      // Also in the form's own banner: a toast lasts five seconds in the opposite corner, and
      // the wizard stays on this step with nothing else to say why.
      setFormError(message);
      toast.error(message, { duration: 5000 });
    },
  });

  /**
   * The Access application for a tunnel route, created once the save has published it: the
   * API refuses a hostname the tunnel does not route yet. Its failure is not the save's. The
   * route is published either way, so what went wrong is shown next to it, instead of turning
   * a published service into a failed save that invites a second one.
   */
  const protectRoute = async (payload: ServicePayload, policyIds: string[]): Promise<AccessOutcome> => {
    if (payload.expose_mode !== 'tunnel' || !payload.tunnel_provider_id || policyIds.length === 0) return null;
    const providerId = payload.tunnel_provider_id;
    const body: AccessProtectBody = { hostname: payload.tunnel_hostname, policy_ids: policyIds };
    try {
      await api.post<AccessProtectResult>(`/providers/${providerId}/access/apps`, body);
      return { ok: true, count: policyIds.length };
    } catch (err) {
      if (getHttpStatus(err) === 403) {
        // The token's own words for the missing permission, as the Access tab names it.
        const item = queryClient
          .getQueryData<TunnelAccessResponse>(TUNNEL_ACCESS_KEY)
          ?.items?.find((entry) => entry.id === providerId);
        return {
          ok: false,
          message: t('monitoring.access.edit_needed', {
            name: item?.edit_permission || 'Access: Apps and Policies Edit',
          }),
        };
      }
      return { ok: false, message: translateApiError(err, t, t('monitoring.access.toast.failed')) };
    }
  };

  const saveService = useMutation({
    mutationFn: async ({
      payload,
      accessPolicyIds,
    }: {
      payload: ServicePayload;
      accessPolicyIds: string[];
      opening: object | null;
    }) => {
      // Both routes publish on the multi-sync targets themselves (`push_extra_targets`) and
      // answer what failed in `errors`. A `POST /push` after them published everything a
      // second time, and reported every failure twice.
      const persisted = isEditMode
        ? await api.put<Record<string, unknown>>(`/services/${serviceId}`, payload)
        : await api.post<Record<string, unknown>>('/services', payload);
      const access = await protectRoute(payload, accessPolicyIds);
      return { persisted, payload, access };
    },
    onSuccess: (result, { opening }) => {
      queryClient.invalidateQueries({ queryKey: ['services'] });
      queryClient.invalidateQueries({ queryKey: ['health'] });
      queryClient.invalidateQueries({ queryKey: ['logs'] });
      if (result.access) queryClient.invalidateQueries({ queryKey: TUNNEL_ACCESS_KEY });

      const allErrors = (isRecordWithErrors(result.persisted) ? result.persisted.errors : []).map(String);
      const host = publicHostOf(result.payload);
      if (result.access && !result.access.ok) toast.error(result.access.message, { duration: 8000 });

      if (allErrors.length === 0) {
        toast.success(
          isEditMode ? t('expose.toast.updated', { host }) : t('expose.toast.created', { host }),
          { duration: 4500 },
        );
      } else {
        const summary = allErrors.slice(0, 2).join('; ');
        const more = allErrors.length > 2 ? t('expose.toast.more', { count: allErrors.length - 2 }) : '';
        toast(
          isEditMode
            ? t('expose.toast.updated_warnings', { errors: summary, more })
            : t('expose.toast.created_warnings', { errors: summary, more }),
          { icon: <TriangleAlert className="h-4 w-4 text-warning" aria-hidden="true" />, duration: 8000 },
        );
      }
      // Saved from a dialog closed since: the toasts above said it, and the form opened after
      // it stays the form, with whatever has been typed in it.
      if (!isOnScreen(opening)) return;
      setSaveOutcome({ host, errors: allErrors, access: result.access });
      setStep('done');
    },
    onError: (err: unknown, { opening }) => {
      const fallback = isEditMode ? t('expose.toast.update_failed') : t('expose.toast.create_failed');
      const message = translateApiError(err, t, fallback);
      // Under the buttons of the dialog that asked, while it is on screen: the step stays put,
      // and a toast in the opposite corner is gone after five seconds with the reason it gave
      // (ux-audit.md § 1.8). Once that dialog is closed, a toast is all that is left to say it,
      // and only then (§ 3.7).
      if (isOnScreen(opening)) setSaveError(message);
      else toast.error(message, { duration: 5000 });
    },
  });

  // Labels alone: one `PATCH`, no checks and no push. No provider holds a label, and
  // `saveService` would publish the whole route again for one.
  const saveLabels = useMutation({
    mutationFn: ({ labels }: { labels: ServiceLabelsPayload; opening: object | null }) =>
      api.patch<Service>(`/services/${serviceId}`, labels),
    onSuccess: (saved, { opening }) => {
      queryClient.invalidateQueries({ queryKey: ['services'] });
      queryClient.invalidateQueries({ queryKey: ['logs'] });
      const host = publicHostOf(saved);
      toast.success(t('expose.toast.labels_updated', { host }), { duration: 4500 });
      if (!isOnScreen(opening)) return;
      setSaveOutcome({ host, errors: [], labelsOnly: true });
      setStep('done');
    },
    onError: (err: unknown, { opening }) => {
      // In the banner as well as the toast while the dialog that asked is on screen: the wizard
      // stays on this step, and a toast in the opposite corner is gone before the operator has
      // looked for it. Closed since, the toast alone.
      const message = translateApiError(err, t, t('expose.toast.update_failed'));
      if (isOnScreen(opening)) setFormError(message);
      toast.error(message, { duration: 5000 });
    },
  });

  // A read, like the checks: asked by a dialog closed since, its answer is dropped, success or
  // failure alike.
  const runDryRun = useMutation({
    mutationFn: (_request: { opening: object | null }) => api.post<DryRunPlan>(`/services/${serviceId}/push/dry-run`),
    onSuccess: (plan, { opening }) => {
      if (isOnScreen(opening)) setDryRun(plan);
    },
    onError: (err: unknown, { opening }) => {
      if (!isOnScreen(opening)) return;
      toast.error(translateApiError(err, t, t('expose.dry_run.failed')), { duration: 5000 });
    },
  });

  const saveTemplate = useMutation({
    mutationFn: ({ body }: { body: TemplateIn; opening: object | null }) => api.post<Template>('/templates', body),
    onSuccess: (created, { body, opening }) => {
      queryClient.invalidateQueries({ queryKey: ['templates'] });
      // The name field of a dialog opened since holds what was typed there, not this name.
      if (isOnScreen(opening)) setTemplateDraftName('');
      toast.success(t('expose.template.saved', { name: created?.name ?? body.name }));
    },
    onError: (err: unknown) => {
      if (isHttpStatus(err, 409)) {
        toast.error(t('expose.template.name_exists'));
        return;
      }
      toast.error(translateApiError(err, t, t('expose.template.save_failed')));
    },
  });

  const buildTemplatePayload = (name: string): TemplateIn => {
    const payload = buildPayload();
    return {
      name: name.trim(),
      description: '',
      forward_scheme: payload.forward_scheme,
      target_port: payload.target_port > 0 ? payload.target_port : null,
      websocket: payload.websocket,
      expose_mode: payload.expose_mode,
      proxy_provider_id: payload.proxy_provider_id,
      dns_provider_id: payload.dns_provider_id,
      tunnel_provider_id: payload.tunnel_provider_id,
      public_target_mode: payload.public_target_mode,
      domain: payload.domain,
      // A target the operator gave or the lookup found, never one worked out from this
      // route: the LAN fallback is this proxy's address or this service's own, and frozen
      // into a template it would stay behind when either changes (ux-audit.md § 1.2).
      // The lookup is read where the payload reads it, for a public zone alone.
      dns_ip: payload.expose_mode === 'proxy_dns' ? formData.dns_ip.trim() || suggestedDnsTarget : '',
      tag_ids: payload.tag_ids,
      // Both halves of the label control, because the form offers both. The tags used to
      // travel alone from here: the environments were chosen on the form, left out of this
      // body, and had no column waiting for them either -- so a template saved from a
      // finished exposure came back naming fewer labels than the exposure it was taken from.
      environment_ids: payload.environment_ids,
      icon_url: payload.icon_url,
    };
  };

  // Only labels moved on an edit: saved directly. See `labelChanges`.
  const labelsOnly = isEditMode ? labelChanges(formData, seedForm()) : null;

  const handleContinue = (e: FormEvent) => {
    e.preventDefault();
    if (labelsOnly) {
      // No provider is asked anything, so neither `validate` nor the checks run: a provider
      // that is down must not block a change it will never see.
      setFormError(null);
      saveLabels.mutate({ labels: labelsOnly, opening: openingRef.current });
      return;
    }
    // Enter submits from inside the target field, so the split made on leaving it has not run:
    // it runs here as well, and its result is what the form shows from now on.
    const parsed = parseTarget(formData.target_ip);
    const form = parsed ? withTargetSplit(formData, parsed) : formData;
    const problems = Object.values(validate(form)).filter((message): message is string => Boolean(message));
    if (problems.length > 0) {
      // Each problem under its own field, and the first of those fields focused. Rendered
      // before the focus moves, so the fields are marked by then. No toast: it said the
      // first problem a second time, away from its field (ux-audit.md § 1.6).
      flushSync(() => {
        setFormData(form);
        setAttempted(true);
      });
      const firstInvalid = formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]');
      if (firstInvalid) {
        setFormError(null);
        firstInvalid.focus();
      } else {
        // The field is not drawn, for one while its list is still loading: the banner says
        // the first problem instead, so a refused "Continue" is never a silent one.
        setFormError(problems[0]);
      }
      return;
    }
    setFormError(null);
    // A refusal said on an earlier review is not this review's.
    setSaveError(null);
    setFormData(form);
    runPreflight.mutate({ payload: buildPayload(form), accessPolicyIds: form.access_policy_ids, opening: openingRef.current });
  };

  const handleSave = () => {
    if (!reviewed) return;
    if (preflight && preflight.summary.blocking_failures > 0) return;
    setSaveError(null);
    saveService.mutate({
      payload: reviewed.payload,
      accessPolicyIds: reviewed.accessPolicyIds,
      opening: openingRef.current,
    });
  };

  const handleSaveTemplate = () => {
    const name = templateDraftName.trim();
    if (!name) {
      toast.error(t('expose.template.name_required'));
      return;
    }
    saveTemplate.mutate({ body: buildTemplatePayload(name), opening: openingRef.current });
  };

  const handleClose = () => {
    setFormData(seedForm());
    setStep('configure');
    setFormError(null);
    setAttempted(false);
    setSaveError(null);
    setPreflight(null);
    setReviewed(null);
    setDryRun(null);
    setSaveOutcome(null);
    setTemplateDraftName('');
    onClose();
  };

  /**
   * Escape closes the dialog even though `persistent` blocks the backdrop, and closing resets
   * every field. A finished exposure -- hostname, target, providers, tags, environments, the
   * preflight already run -- is several minutes of work, and one stray key threw all of it
   * away, with no undo and no trace of what was in the form. The reset is now gated on the
   * wizard having something to lose; Cancel goes through the same gate, so the two ways out
   * of the first step behave alike.
   *
   * `done` is deliberately never dirty: the push has happened, that panel is a receipt, and
   * making the operator confirm the closing of a receipt would be noise. `review` always is
   * -- reaching it costs a preflight round-trip, even when nothing was typed on an edit.
   */
  const isDirty =
    step !== 'done' &&
    (step === 'review' ||
      templateDraftName.trim() !== '' ||
      JSON.stringify(formData) !== JSON.stringify(seedForm()));

  const requestClose = async () => {
    if (
      isDirty &&
      !(await confirm({
        title: t('expose.discard.title'),
        message: t('expose.discard.message'),
        confirmLabel: t('expose.discard.confirm'),
        cancelLabel: t('expose.discard.cancel'),
        variant: 'danger',
      }))
    ) {
      return;
    }
    handleClose();
  };

  const checkLabel = (name: string): string => {
    const key = `expose.preflight.check.${name}`;
    const label = t(key);
    return label === key ? name : label;
  };

  /**
   * What the check found, in the reader's language. The API sends the English sentence in
   * `detail` and the short code it was written from in `detail_key`; the code wins when this
   * build knows it, and the sentence stands in otherwise.
   */
  const checkDetail = (check: PreflightCheck): string => preflightDetailText(check, t);

  const blockingFailures = preflight?.summary.blocking_failures ?? 0;
  const warningCount = preflight?.summary.warnings ?? 0;
  // Only what the save acts on, read from what the review froze: policies ticked on a tunnel,
  // then left behind by a switch to another mode, are not sent, and the host named is the one
  // the Access application will be made for.
  const pendingAccess =
    reviewed &&
    reviewed.payload.expose_mode === 'tunnel' &&
    reviewed.payload.tunnel_provider_id &&
    reviewed.accessPolicyIds.length > 0
      ? { count: reviewed.accessPolicyIds.length, host: reviewed.payload.tunnel_hostname }
      : null;
  const doneWithWarnings = Boolean(saveOutcome && (saveOutcome.errors.length > 0 || saveOutcome.access?.ok === false));

  const stepLabels: Record<Step, string> = {
    configure: t('expose.steps.configure'),
    review: t('expose.steps.review'),
    done: t('expose.steps.done'),
  };

  const title = isEditMode ? t('expose.title.edit') : t('expose.title.create');
  // An edit that moves labels alone contacts no provider, so the line under the title says
  // so, from the first label moved to the receipt. It used to promise the push on every edit,
  // and kept as it was, it would now sit above a receipt saying no provider was contacted.
  const editDescription =
    labelsOnly !== null || saveOutcome?.labelsOnly === true
      ? t('expose.labels_only.hint')
      : t('expose.description.edit');
  const description = templateName
    ? t('expose.from_template', { name: templateName })
    : isEditMode
      ? editDescription
      : t('expose.description.create');

  let footer: ReactNode;
  if (step === 'configure') {
    footer = (
      <>
        <Button variant="ghost" onClick={() => void requestClose()}>
          {t('common.cancel')}
        </Button>
        {labelsOnly ? (
          <Button type="submit" form={formId} loading={saveLabels.isPending} leftIcon={<Check />}>
            {t('expose.labels_only.save')}
          </Button>
        ) : (
          <Button
            type="submit"
            form={formId}
            loading={runPreflight.isPending}
            disabled={isLoadingProviders}
            rightIcon={<ArrowRight />}
          >
            {t('expose.continue')}
          </Button>
        )}
      </>
    );
  } else if (step === 'review') {
    footer = (
      <>
        {/* Neither "Back" nor "Check again" while the save is out: its answer would land on a
            form or a review it no longer belongs to, said there under the wrong body, or the
            route saved twice. The dialog can still be closed, and the answer then goes to a
            toast. Nor "Back" while "Check again" runs: its answer would bring the review back
            on the body sent at the click, over a form changed since. Closing stays open, and
            that answer is then dropped. */}
        <Button
          variant="ghost"
          disabled={saveService.isPending || runPreflight.isPending}
          onClick={() => {
            setSaveError(null);
            setReviewed(null);
            setStep('configure');
          }}
          className="mr-auto"
        >
          {t('common.back')}
        </Button>
        <Button
          variant="outline"
          leftIcon={<RefreshCw />}
          loading={runPreflight.isPending}
          disabled={saveService.isPending}
          onClick={() => {
            setSaveError(null);
            // Checked again as it would be sent now, and that body and those policies become
            // the pair reviewed.
            runPreflight.mutate({
              payload: buildPayload(),
              accessPolicyIds: formData.access_policy_ids,
              opening: openingRef.current,
            });
          }}
        >
          {t('expose.preflight.rerun')}
        </Button>
        <Button
          onClick={handleSave}
          loading={saveService.isPending}
          disabled={!reviewed || blockingFailures > 0 || runPreflight.isPending}
          leftIcon={<Check />}
        >
          {isEditMode ? t('expose.save_route') : t('expose.create_route')}
        </Button>
      </>
    );
  } else {
    footer = <Button onClick={handleClose}>{t('ui.close')}</Button>;
  }

  return (
    <Modal
      open={isOpen}
      onClose={() => void requestClose()}
      title={title}
      description={description}
      icon={<Network />}
      size="xl"
      persistent
      footer={footer}
      bodyClassName="space-y-6"
    >
      <Stepper
        current={step}
        label={t('expose.steps.label')}
        labels={stepLabels}
        skipped={saveOutcome?.labelsOnly ? ['review'] : []}
      />

      {step === 'configure' && (
        // `noValidate`: the browser's own bubbles said a third time, one field at a time, what
        // `validate` now says under every field at once (ux-audit.md § 1.6). It also lets the
        // labels-only save through, which sends none of the route fields.
        <form
          ref={formRef}
          id={formId}
          noValidate
          onSubmit={handleContinue}
          className="space-y-8 animate-in fade-in animate-duration-200"
        >
          {formError && (
            <div ref={formErrorRef}>
              <InlineAlert tone="danger" onDismiss={() => setFormError(null)}>
                {formError}
              </InlineAlert>
            </div>
          )}

          <ServiceForm
            formData={formData}
            setFormData={setFormData}
            providers={providers}
            domains={domains}
            providersError={providersError}
            isRefetchingProviders={providersError && isFetchingProviders}
            refetchProviders={() => void refetchProviders()}
            domainsError={domainsError}
            tagsError={tagsError}
            environmentsError={environmentsError}
            isLoadingProviders={isLoadingProviders}
            isLoadingDomains={isLoadingDomains}
            providerTypeMap={providerTypeMap}
            targetSuggestion={targetSuggestion}
            isFetchingTargetSuggestion={isFetchingTargetSuggestion}
            targetSuggestionError={targetSuggestionError}
            refetchTargetSuggestion={refetchTargetForThisOpening}
            tags={tags}
            environments={environments}
            isLoadingTaxonomy={isLoadingTags || isLoadingEnvironments}
            errors={fieldErrors}
            proxyDnsTarget={proxyDnsTarget}
          />

          <ServicePreview
            formData={formData}
            selectedProxy={selectedProxy}
            selectedDns={selectedDns}
            selectedTunnel={selectedTunnel}
            selectedExtraProxies={selectedExtraProxies}
            selectedExtraDns={selectedExtraDns}
          />

          {/* Save as template: the current setup minus subdomain and target, reusable from Templates. */}
          <section className="space-y-3 rounded-2xl border border-dashed border-border bg-muted/30 p-4">
            <SectionHeading
              as="h3"
              size="sm"
              icon={<BookmarkPlus />}
              title={t('expose.template.title')}
              description={t('expose.template.description')}
            />
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                aria-label={t('expose.template.name_label')}
                placeholder={t('expose.template.name_placeholder')}
                value={templateDraftName}
                maxLength={64}
                wrapperClassName="flex-1"
                onChange={(e) => setTemplateDraftName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    handleSaveTemplate();
                  }
                }}
              />
              <Button
                variant="secondary"
                leftIcon={<BookmarkPlus />}
                loading={saveTemplate.isPending}
                disabled={!templateDraftName.trim()}
                onClick={handleSaveTemplate}
              >
                {t('expose.template.save')}
              </Button>
            </div>
          </section>
        </form>
      )}

      {step === 'review' && preflight && (
        <div className="space-y-6 animate-in fade-in animate-duration-200">
          {saveError && (
            <div ref={saveErrorRef}>
              <InlineAlert tone="danger" onDismiss={() => setSaveError(null)}>
                {saveError}
              </InlineAlert>
            </div>
          )}
          {blockingFailures > 0 ? (
            <InlineAlert tone="danger" title={t('expose.preflight.blocked_title', { count: blockingFailures })}>
              {t('expose.preflight.blocked_body', { count: blockingFailures })}
            </InlineAlert>
          ) : warningCount > 0 ? (
            <InlineAlert tone="warning" title={t('expose.preflight.warnings_title', { count: warningCount })}>
              {t('expose.preflight.warnings_body')}
            </InlineAlert>
          ) : (
            <InlineAlert tone="success" title={t('expose.preflight.passed_title')}>
              {t('expose.preflight.passed_body')}
            </InlineAlert>
          )}
          {pendingAccess && (
            <InlineAlert tone="info">
              {t('expose.access.review', { count: pendingAccess.count, host: pendingAccess.host })}
            </InlineAlert>
          )}

          <section className="space-y-3">
            <SectionHeading
              as="h3"
              size="sm"
              title={t('expose.preflight.title')}
              description={t('expose.preflight.description', { host: preflight.public_host || fqdnPreview })}
            >
              <span className="text-xs text-muted-foreground">
                {t('expose.preflight.summary', {
                  checks: t('expose.preflight.checks_count', { count: preflight.summary.total }),
                  blocking: t('expose.preflight.blocking_count', { count: blockingFailures }),
                  warnings: t('expose.preflight.warnings_count', { count: warningCount }),
                })}
              </span>
            </SectionHeading>
            <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
              {preflight.checks.map((check, index) => {
                const tone = checkTone(check);
                const detail = checkDetail(check);
                return (
                  // The name is not a key: the server emits one `extra_proxy_provider` line
                  // per extra proxy and one `extra_dns_provider` line per extra DNS server,
                  // so a route published on two spare proxies really does produce two list
                  // items called the same thing. Measured on React 19.2.8: both <li> render,
                  // so nothing vanishes from the panel -- but React logs "Encountered two
                  // children with the same key", warns that such children "may be duplicated
                  // and/or omitted", and calls the behaviour unsupported. What a shared key
                  // costs today is identity, which is the only thing a key is for: with one
                  // key for two rows the reconciler fell back to matching by position, so
                  // swapping the two lines left each row's state and DOM node on the other
                  // line, and a later change of keys left a stale third <li> standing for two
                  // lines of data. A panel whose whole job is to say what the save will touch
                  // cannot afford a row that belongs to a different target. The list is
                  // rebuilt whole on every preflight and never reordered, so the index is
                  // stable enough to carry the name.
                  <li key={`${check.name}:${index}`} className="flex items-start gap-3 px-4 py-3">
                    <span aria-hidden="true" className="mt-0.5 shrink-0">
                      {CHECK_ICONS[tone]}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-medium text-foreground">{checkLabel(check.name)}</p>
                        <Badge tone={tone} size="sm">
                          {tone === 'success'
                            ? t('expose.preflight.state.pass')
                            : tone === 'danger'
                              ? t('expose.preflight.state.blocking')
                              : t('expose.preflight.state.warning')}
                        </Badge>
                      </div>
                      {detail && <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p>}
                    </div>
                  </li>
                );
              })}
              {preflight.checks.length === 0 && (
                <li className="px-4 py-6 text-center text-sm text-muted-foreground">{t('expose.preflight.no_checks')}</li>
              )}
            </ul>
          </section>

          {isEditMode && (
            <section className="space-y-3">
              <SectionHeading
                as="h3"
                size="sm"
                icon={<FlaskConical />}
                title={t('expose.dry_run.title')}
                description={t('expose.dry_run.description')}
              >
                <Button
                  size="sm"
                  variant="outline"
                  leftIcon={<FlaskConical />}
                  loading={runDryRun.isPending}
                  onClick={() => runDryRun.mutate({ opening: openingRef.current })}
                >
                  {dryRun ? t('expose.dry_run.rerun') : t('expose.dry_run.run')}
                </Button>
              </SectionHeading>

              {dryRun && (
                <div className="space-y-4 rounded-2xl border border-border bg-card p-4 animate-in fade-in animate-duration-200">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={dryRun.would_change ? 'warning' : 'success'} dot>
                      {dryRun.would_change ? t('expose.dry_run.would_change') : t('expose.dry_run.no_change')}
                    </Badge>
                    <span className="font-mono text-xs text-muted-foreground">{dryRun.public_host}</span>
                    {dryRun.dns_target && (
                      <span className="text-xs text-muted-foreground">
                        {t('expose.dry_run.dns_target', {
                          target: dryRun.dns_target,
                          source: publicTargetSourceLabel(dryRun.dns_target_source, t),
                        })}
                      </span>
                    )}
                  </div>

                  {dryRun.withheld && (
                    <InlineAlert tone="info" title={t('expose.dry_run.withheld')} />
                  )}

                  {dryRun.proxy_actions.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        {t('expose.dry_run.proxy_actions')}
                      </p>
                      <ul className="space-y-1.5">
                        {dryRun.proxy_actions.map((action) => (
                          <li key={`proxy-${action.provider_id}`} className="flex items-center gap-2 text-sm">
                            <ProviderLogo type={action.provider_type} className="h-4 w-4" />
                            <span className="font-medium text-foreground">{action.provider_name}</span>
                            <Badge size="sm" tone={PLAN_ACTION_TONE[action.action] ?? 'info'}>
                              {t(`expose.dry_run.action.${action.action}`)}
                            </Badge>
                            <span className="truncate font-mono text-xs text-muted-foreground">
                              {action.target_host}
                              {action.target_origin ? ` → ${action.target_origin}` : ''}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {dryRun.dns_actions.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        {t('expose.dry_run.dns_actions')}
                      </p>
                      <ul className="space-y-1.5">
                        {dryRun.dns_actions.map((action) => (
                          <li key={`dns-${action.provider_id}`} className="flex items-center gap-2 text-sm">
                            <ProviderLogo type={action.provider_type} className="h-4 w-4" />
                            <span className="font-medium text-foreground">{action.provider_name}</span>
                            <Badge size="sm" tone={PLAN_ACTION_TONE[action.action] ?? 'info'}>
                              {t(`expose.dry_run.action.${action.action}`)}
                            </Badge>
                            <span className="truncate font-mono text-xs text-muted-foreground">
                              {action.domain}
                              {action.target ? ` → ${action.target}` : ''}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {dryRun.service_updates.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        {t('expose.dry_run.service_updates')}
                      </p>
                      <ul className="space-y-1.5">
                        {dryRun.service_updates.map((update) => (
                          <li key={update.field} className="flex flex-wrap items-center gap-2 text-sm">
                            <span className="font-mono text-xs text-foreground">{update.field}</span>
                            <span className="font-mono text-xs text-muted-foreground line-through">
                              {String(update.old ?? '-')}
                            </span>
                            <ArrowRight aria-hidden="true" className="h-3 w-3 text-muted-foreground" />
                            <span className="font-mono text-xs text-foreground">{String(update.new ?? '-')}</span>
                            <span className="text-xs text-muted-foreground">({update.source})</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {dryRun.warnings.length > 0 && (
                    <InlineAlert tone="warning" title={t('expose.dry_run.warnings')}>
                      <ul className="list-disc space-y-0.5 pl-4">
                        {dryRun.warnings.map((warning, index) => (
                          <li key={index}>{warning}</li>
                        ))}
                      </ul>
                    </InlineAlert>
                  )}
                  {dryRun.errors.length > 0 && (
                    <InlineAlert tone="danger" title={t('expose.dry_run.errors')}>
                      <ul className="list-disc space-y-0.5 pl-4">
                        {dryRun.errors.map((error, index) => (
                          <li key={index}>{error}</li>
                        ))}
                      </ul>
                    </InlineAlert>
                  )}
                  {dryRun.proxy_actions.length === 0 &&
                    dryRun.dns_actions.length === 0 &&
                    dryRun.service_updates.length === 0 && (
                      <p className="text-sm text-muted-foreground">{t('expose.dry_run.empty')}</p>
                    )}
                  <p className="text-xs text-muted-foreground">{t('expose.dry_run.note')}</p>
                </div>
              )}
            </section>
          )}
        </div>
      )}

      {step === 'done' && saveOutcome && (
        <div className="space-y-5 py-4 text-center animate-in fade-in zoom-in-95 animate-duration-200">
          <span
            aria-hidden="true"
            className={cn(
              'mx-auto inline-flex h-14 w-14 items-center justify-center rounded-2xl',
              doneWithWarnings ? 'bg-warning/10 text-warning' : 'bg-success/10 text-success',
            )}
          >
            {doneWithWarnings ? <TriangleAlert className="h-7 w-7" /> : <CircleCheck className="h-7 w-7" />}
          </span>
          <div className="space-y-1">
            <h3 className="text-lg font-semibold text-foreground">
              {saveOutcome.labelsOnly
                ? t('expose.done.labels_title')
                : isEditMode
                  ? t('expose.done.updated_title')
                  : t('expose.done.created_title')}
            </h3>
            <p className="text-sm text-muted-foreground">
              {withHostHighlighted(
                saveOutcome.labelsOnly
                  ? t('expose.done.labels_body', { host: saveOutcome.host })
                  : t('expose.done.body', { host: saveOutcome.host }),
                saveOutcome.host,
              )}
            </p>
          </div>
          {saveOutcome.access?.ok && (
            <InlineAlert tone="success" className="text-left">
              {t('expose.access.done', { count: saveOutcome.access.count, host: saveOutcome.host })}
            </InlineAlert>
          )}
          {saveOutcome.access && !saveOutcome.access.ok && (
            <InlineAlert tone="warning" title={t('expose.access.done_failed')} className="text-left">
              <p>{saveOutcome.access.message}</p>
              <Link
                to="/monitoring?tab=access"
                onClick={handleClose}
                className="mt-1 inline-block font-medium text-primary hover:underline"
              >
                {t('expose.access.open_tab')}
              </Link>
            </InlineAlert>
          )}
          {saveOutcome.errors.length > 0 && (
            <InlineAlert tone="warning" title={t('expose.done.warnings_title')} className="text-left">
              <ul className="list-disc space-y-0.5 pl-4">
                {saveOutcome.errors.map((error, index) => (
                  <li key={index}>{error}</li>
                ))}
              </ul>
            </InlineAlert>
          )}
        </div>
      )}
      {ConfirmDialogElement}
    </Modal>
  );
}
