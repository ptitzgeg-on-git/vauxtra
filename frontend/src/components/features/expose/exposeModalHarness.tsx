/**
 * The expose modal mounted for a test, in either mode, over one API that answers from `world`.
 *
 * Each older test file mocks `@/api/client` with the handful of routes it needs, and none of
 * them opens the create form: the provider lists, the suggestion, the checks and the save
 * were each answered somewhere, never together. Here they all are, read from one object that
 * every test starts again from the same state and changes in place before it mounts.
 *
 * The client is spied on, not mocked. `vitest.config.ts` already swaps `@/api/client` for
 * `src/test/apiStub.ts`, and the modal and its hooks read `api.get` off that one shared object
 * at call time, so the spies set in `beforeEach` are what they call. A `vi.mock` factory here
 * would be hoisted into every file that imports this one.
 *
 * `t()` returns the key: `renderWithProviders` leaves `I18nProvider` out, so the assertions
 * name keys, and a regular expression stands in for a name that ends in "(ui.required)".
 *
 * Every top-level name is camelCase on purpose: `react-refresh` reads a `.tsx` module that
 * declares a capitalised function or call as a component file that exports other things.
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import { focusManager, QueryClient } from '@tanstack/react-query';
import { beforeEach, expect, vi } from 'vitest';
import { api } from '@/api/client';
import { HttpError } from '@/api/httpError';
import { makeQueryClient, renderWithProviders } from '@/test/render';
import { openOption, optionLabels } from '@/test/select';
import type {
  DryRunPlan,
  Environment,
  PreflightRequest,
  PreflightResult,
  Provider,
  ProviderTypesResponse,
  Service,
  ServicePayload,
  Tag,
  TunnelAccessItem,
  TunnelAccessResponse,
} from '@/types/api';
import { ExposeModal } from './ExposeModal';
import type { FormState } from './types';

export type ApiMethod = 'get' | 'post' | 'put' | 'patch' | 'delete';

const integration = (id: number, name: string, type: string, url = ''): Provider => ({
  id,
  name,
  type,
  url,
  username: '',
  enabled: true,
  extra: {},
  created_at: '2026-01-01 09:00:00',
});

/**
 * One instance's integrations, two of each role so that a change of mind has somewhere to go.
 * `/providers/types` answers `{}`, so the roles come from the capability floor in
 * `lib/providers.ts`: `npm` proxies, `pihole` and `adguard` resolve on the LAN, `cloudflare`
 * is a public zone that can find its own target, `cloudflare_tunnel` routes and tunnels.
 */
export const integrations = {
  npmFront: integration(7, 'npm-front', 'npm', 'http://10.0.0.30:81'),
  npmBack: integration(6, 'npm-back', 'npm', 'http://10.0.0.31:81'),
  piholeLan: integration(3, 'pihole-lan', 'pihole', 'http://192.168.1.2'),
  adguardLan: integration(4, 'adguard-lan', 'adguard', 'http://192.168.1.3'),
  cfEdge: integration(8, 'cf-edge', 'cloudflare'),
  cfTunnel: integration(9, 'cf-tunnel', 'cloudflare_tunnel'),
  cfTunnelB: integration(10, 'cf-tunnel-b', 'cloudflare_tunnel'),
};

/** What `GET /providers/tunnels/access` says of one tunnel: readable, with one policy to offer. */
export const tunnelAccessItem = (provider: Provider, policy: { id: string; name: string }): TunnelAccessItem => ({
  id: provider.id,
  name: provider.name,
  permission: 'ok',
  reason: '',
  read_permission: 'Access: Apps and Policies Read',
  edit_permission: 'Access: Apps and Policies Edit',
  application_count: 0,
  policies: [{ id: policy.id, name: policy.name, decision: 'allow', everyone: false, app_count: 0 }],
  counts: { protected: 0, partial: 0, open: 0, unknown: 0 },
  hosts: [],
});

/** A saved route behind `npm-front` with a LAN resolver: what the edit form opens on. */
export const savedService = (overrides: Partial<Service> = {}): Service => ({
  id: 42,
  subdomain: 'grafana',
  domain: 'example.com',
  target_ip: '10.0.0.20',
  target_port: 3000,
  forward_scheme: 'http',
  websocket: false,
  expose_mode: 'proxy_dns',
  public_target_mode: 'manual',
  auto_update_dns: false,
  tunnel_hostname: '',
  dns_ip: '10.0.0.30',
  npm_host_id: null,
  dns_provider_id: 3,
  proxy_provider_id: 7,
  tunnel_provider_id: null,
  enabled: true,
  status: 'ok',
  last_checked: null,
  created_at: '2026-01-01 09:00:00',
  tags: [],
  environments: [],
  ...overrides,
});

export interface ExposeWorld {
  providers: Provider[];
  domains: string[];
  providerTypes: ProviderTypesResponse;
  tags: Tag[];
  environments: Environment[];
  tunnelAccess: TunnelAccessResponse;
  services: Service[];
  /** The address `/services/public-target/suggest` recommends, `''` when it finds none. */
  suggestion: string;
  /** `'fails'` rejects the lookup, `'pending'` never settles it. */
  suggestionFate: 'answers' | 'fails' | 'pending';
  preflight: (body: PreflightRequest) => PreflightResult;
  /** What `POST /services` and `PUT /services/<id>` answer. */
  saveResult: { ok: boolean; errors: string[] };
  /** What `GET /templates/<id>/apply` answers. */
  templateApply: Record<string, unknown>;
  dryRun: DryRunPlan;
  /** Calls answered with an HTTP error instead, keyed `"post /services"`. */
  failures: Record<string, { status: number; data: unknown }>;
}

const passingPreflight = (body: PreflightRequest): PreflightResult => ({
  ok: true,
  public_host: `${body.subdomain}.${body.domain}`,
  checks: [],
  summary: { blocking_failures: 0, warnings: 0, total: 0 },
});

const freshWorld = (): ExposeWorld => ({
  providers: Object.values(integrations),
  domains: ['example.com'],
  providerTypes: {},
  tags: [],
  environments: [],
  tunnelAccess: { total: 0, items: [] },
  services: [],
  suggestion: '10.0.0.40',
  suggestionFate: 'answers',
  preflight: passingPreflight,
  saveResult: { ok: true, errors: [] },
  templateApply: {},
  dryRun: {
    service_id: 42,
    mode: 'proxy_dns',
    public_host: 'grafana.example.com',
    proxy_actions: [],
    dns_actions: [],
    service_updates: [],
    warnings: [],
    errors: [],
    dns_target: '',
    dns_target_source: '',
    would_change: false,
    ok: true,
    withheld: false,
  },
  failures: {},
});

/** The state every test starts from; change it before mounting, or mid-test for a next answer. */
export const world: ExposeWorld = freshWorld();

export interface ApiCall {
  method: ApiMethod;
  url: string;
  /** The URL without its query string. */
  path: string;
  body: unknown;
}

/** Every call the modal made in this test, in order. */
export const apiLog: ApiCall[] = [];

const never: Promise<never> = new Promise(() => {});

function answer(method: ApiMethod, path: string, body: unknown): unknown {
  if (method === 'get') {
    switch (path) {
      case '/providers':
        return world.providers;
      case '/domains':
        return world.domains;
      case '/providers/types':
        return world.providerTypes;
      case '/tags':
        return world.tags;
      case '/environments':
        return world.environments;
      case '/providers/tunnels/access':
        return world.tunnelAccess;
      case '/services':
        return world.services;
      case '/settings':
        return {};
      case '/services/public-target/suggest':
        if (world.suggestionFate === 'fails') throw new Error('public target lookup refused');
        if (world.suggestionFate === 'pending') return never;
        return {
          candidates: world.suggestion ? [{ value: world.suggestion, source: 'server_public_ip' }] : [],
          recommended: world.suggestion,
        };
    }
    if (/^\/templates\/[^/]+\/apply$/.test(path)) return world.templateApply;
    return [];
  }
  if (method === 'post') {
    if (path === '/services/preflight') return world.preflight(body as PreflightRequest);
    if (path === '/services') return world.saveResult;
    if (path === '/templates') return { id: 99, ...(body as object) };
    if (/^\/services\/\d+\/push\/dry-run$/.test(path)) return world.dryRun;
    if (/^\/providers\/\d+\/access\/apps$/.test(path)) {
      const sent = body as { hostname: string; policy_ids: string[] };
      return { ok: true, hostname: sent.hostname, application_id: 'app-1', policy_ids: sent.policy_ids };
    }
    return { ok: true };
  }
  if (method === 'put' && /^\/services\/\d+$/.test(path)) return world.saveResult;
  return { ok: true };
}

const respond = (method: ApiMethod) => async (url: string, body?: unknown) => {
  const path = url.split('?')[0];
  apiLog.push({ method, url, path, body });
  const failure = world.failures[`${method} ${path}`];
  if (failure) {
    throw new HttpError(`${method.toUpperCase()} ${path} answered ${failure.status}`, url, {
      status: failure.status,
      data: failure.data,
      headers: {},
    });
  }
  return answer(method, path, body);
};

/**
 * Registers the `beforeEach` that resets `world` and `apiLog` and spies on the client. Call
 * it once, at the top of a test file or inside its `describe`.
 */
export function installExposeApi(): void {
  beforeEach(() => {
    Object.assign(world, freshWorld());
    apiLog.length = 0;
    // jsdom has no layout, so no `scrollIntoView` either; a fresh one per test can be asked.
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(api, 'get').mockImplementation(respond('get') as unknown as typeof api.get);
    vi.spyOn(api, 'post').mockImplementation(respond('post') as unknown as typeof api.post);
    vi.spyOn(api, 'put').mockImplementation(respond('put') as unknown as typeof api.put);
    vi.spyOn(api, 'patch').mockImplementation(respond('patch') as unknown as typeof api.patch);
    vi.spyOn(api, 'delete').mockImplementation(respond('delete') as unknown as typeof api.delete);
  });
}

/** Makes one route answer `status` with `data` as its body, until the test ends. */
export function failWith(method: ApiMethod, path: string, status: number, data: unknown = { detail: 'refused' }): void {
  world.failures[`${method} ${path}`] = { status, data };
}

export function callsTo(method: ApiMethod, path: string | RegExp): ApiCall[] {
  return apiLog.filter((call) => call.method === method && (typeof path === 'string' ? call.path === path : path.test(call.path)));
}

export const preflightBodies = (): PreflightRequest[] =>
  callsTo('post', '/services/preflight').map((call) => call.body as PreflightRequest);

/** The bodies of `POST /services` and `PUT /services/<id>`, the two saves that publish. */
export const savedBodies = (): ServicePayload[] =>
  [...callsTo('post', '/services'), ...callsTo('put', /^\/services\/\d+$/)].map((call) => call.body as ServicePayload);

/** A client whose reads go stale at once, for the tests about reading again. */
export function makeEagerQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

export interface MountOptions {
  initialState?: FormState | null;
  templateName?: string | null;
  queryClient?: QueryClient;
}

function mount(mode: 'create' | 'edit', service: Service | null, options: MountOptions) {
  const { initialState = null, templateName = null, queryClient = makeQueryClient() } = options;
  const user = userEvent.setup();
  const onClose = vi.fn();
  const modal = (open: boolean) => (
    <ExposeModal
      isOpen={open}
      onClose={onClose}
      mode={mode}
      service={service}
      initialState={initialState}
      templateName={templateName}
    />
  );
  const view = renderWithProviders(modal(true), { queryClient });
  return {
    ...view,
    user,
    onClose,
    /** Closed and opened again as one instance, the way `Dashboard` keeps it mounted. */
    reopen: () => {
      view.rerender(modal(false));
      view.rerender(modal(true));
    },
    /** Closed by its parent and left mounted, as `Dashboard` does after `onClose`. */
    close: () => view.rerender(modal(false)),
  };
}

export const openCreate = (options: MountOptions = {}) => mount('create', null, options);
export const openEdit = (service: Service, options: MountOptions = {}) => mount('edit', service, options);

/** The form's controls, found the way an operator names them. */
export const field = {
  subdomain: () => screen.getByRole('textbox', { name: /^expose\.field\.subdomain\b/ }),
  domain: () => screen.getByRole('combobox', { name: /^expose\.field\.domain\b/ }),
  target: () => screen.getByRole('textbox', { name: /^expose\.field\.target\b/ }),
  port: () => screen.getByRole('textbox', { name: /^expose\.field\.port\b/ }),
  scheme: () => screen.getByRole('combobox', { name: /^expose\.field\.scheme\b/ }),
  tunnelHostname: () => screen.getByRole('textbox', { name: /^expose\.field\.tunnel_hostname\b/ }),
  dnsTarget: () => screen.getByRole('textbox', { name: /^expose\.field\.dns_target_(local|external)\b/ }),
  primaryProxy: () => screen.getByRole('combobox', { name: /^expose\.field\.primary_proxy\b/ }),
  primaryDns: () => screen.getByRole('combobox', { name: /^expose\.field\.primary_dns\b/ }),
  tunnelProvider: () => screen.getByRole('combobox', { name: /^expose\.field\.tunnel_provider\b/ }),
  /** A mode card's radio, found by its value: its name runs title and description together. */
  mode: (value: 'dns_only' | 'dns_proxy' | 'tunnel') => {
    const radio = screen.getAllByRole('radio').find((input) => (input as HTMLInputElement).value === value);
    if (!radio) throw new Error(`No mode card "${value}"`);
    return radio;
  },
};

/**
 * Picks `name` in a `Select`, once the list holds it. The wait reads the closed list and
 * opens nothing, as `@/test/select` asks of anything inside `waitFor`.
 */
export async function pickOption(combobox: () => HTMLElement, name: string): Promise<void> {
  await waitFor(() => expect(optionLabels(combobox())).toContain(name));
  fireEvent.click(openOption(combobox(), name));
}

/** Waits for a mode card to accept a click (its role needs the integrations read), then picks it. */
export async function chooseMode(user: UserEvent, value: 'dns_only' | 'dns_proxy' | 'tunnel'): Promise<void> {
  await waitFor(() => expect(field.mode(value)).toBeEnabled());
  await user.click(field.mode(value));
}

/**
 * Types the domain and tabs out, so its suggestion list is closed again. Not Escape: with no
 * suggestion left open to close, the dialog takes the key and asks to discard the form.
 */
export async function typeDomain(user: UserEvent, domain: string): Promise<void> {
  await user.type(field.domain(), domain);
  await user.tab();
}

/** "Continue", clicked like an operator would: once the integrations are read, by the pointer. */
export async function clickContinue(user: UserEvent): Promise<void> {
  const button = await screen.findByRole('button', { name: 'expose.continue' });
  await waitFor(() => expect(button).toBeEnabled());
  await user.click(button);
}

/** Continue, then wait for the checks to have been asked. */
export async function continueToReview(user: UserEvent): Promise<PreflightRequest> {
  const before = preflightBodies().length;
  await clickContinue(user);
  await waitFor(() => expect(preflightBodies()).toHaveLength(before + 1));
  return preflightBodies()[before];
}

/** Runs the window-focus round trip TanStack Query listens to, and puts the default back. */
export function refocusWindow(): void {
  act(() => {
    focusManager.setFocused(false);
    focusManager.setFocused(true);
  });
  focusManager.setFocused(undefined);
}

/**
 * Enter in a field of the form, answered the way a browser answers it: the form is submitted
 * through its default button, and the field is never left. user-event does not get there on
 * its own here, because "Continue" sits in the dialog's footer, outside the `<form>`, and
 * joins it through `form=`.
 */
export async function pressEnterIn(input: HTMLElement): Promise<void> {
  const button = await screen.findByRole('button', { name: 'expose.continue' });
  await waitFor(() => expect(button).toBeEnabled());
  const form = (input as HTMLInputElement).form;
  if (!form || !(button instanceof HTMLButtonElement)) throw new Error('The field and Continue are not one form');
  act(() => form.requestSubmit(button));
}

type ClientCall = (url: string, ...rest: unknown[]) => Promise<unknown>;

/**
 * Keeps every call to `method path` waiting until `release()`, which answers them from
 * `world` as it stands by then: for what the screen does while a request is in flight.
 * `held` lists the URLs caught so far; a held call reaches `apiLog` once it is answered.
 */
export function holdCalls(method: ApiMethod, path: string): { held: string[]; release: () => Promise<void> } {
  const spy = vi.mocked((api as unknown as Record<ApiMethod, ClientCall>)[method]);
  const answerNow = spy.getMockImplementation();
  if (!answerNow) throw new Error(`api.${method} is not spied on: installExposeApi() comes first`);
  let open!: () => void;
  const gate = new Promise<void>((resolve) => {
    open = resolve;
  });
  const held: string[] = [];
  spy.mockImplementation(async (url: string, ...rest: unknown[]) => {
    if (url.split('?')[0] === path) {
      held.push(url);
      await gate;
    }
    return answerNow(url, ...rest);
  });
  return { held, release: () => act(async () => open()) };
}
