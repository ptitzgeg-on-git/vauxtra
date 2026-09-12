// ---------------------------------------------------------------------------
// Vauxtra API types — keep in sync with the FastAPI handlers in app/api/*.py
// ---------------------------------------------------------------------------
//
// Conventions the backend follows, so the types do too:
//  - timestamps are naive UTC text `YYYY-MM-DD HH:MM:SS` (SQLite `datetime('now')`);
//    parse them with `lib/format.ts#parseBackendTimestamp`, never with `new Date(text)`
//  - SQLite booleans come back as 0/1 on rows read straight from a table and as real
//    booleans where a handler coerces them, hence `boolean | number` on row types
//  - every setting value is text, and a failed call carries `detail` in the body
//    (string, pydantic list, or object) — `lib/errors.ts` reads all three shapes

export type ForwardScheme = 'http' | 'https';
export type ExposeMode = 'proxy_dns' | 'tunnel';
export type PublicTargetMode = 'manual' | 'auto';
export type ServiceStatus = 'ok' | 'error' | 'unknown';
export type ProviderRole = 'proxy' | 'dns';
/**
 * Levels written by `add_log` (`app/models.py`), which normalises `warn` to `warning`.
 * Rows written before it did still carry `warn`, so it stays in the union; the column is
 * free text, so treat anything outside it as `info` when rendering. No row has ever
 * carried `ok`.
 */
export type LogLevel = 'info' | 'warn' | 'warning' | 'error';

/** `{ok: true}` — what most mutations answer with. */
export interface OkResponse {
  ok: boolean;
}

// ---------------------------------------------------------------------------
// Tags & Environments
// ---------------------------------------------------------------------------

/** Colour names accepted by tags and environments (`app/validators.py`). Anything else is stored as `blue`. */
export type TagColor =
  | 'blue'
  | 'teal'
  | 'green'
  | 'red'
  | 'orange'
  | 'purple'
  | 'cyan'
  | 'yellow'
  | 'pink'
  | 'lime'
  | 'indigo'
  | 'azure'
  | 'secondary'
  | 'dark';

export interface Tag {
  id: number;
  name: string;
  color: string;
}

export interface Environment {
  id: number;
  name: string;
  color: string;
}

/** Body of `POST /api/tags` and `PUT /api/tags/{tid}`; both answer with the `Tag`. `name` ≤ 32 chars. */
export interface TagIn {
  name: string;
  color?: TagColor | string;
}

/** Body of `POST /api/environments` and `PUT /api/environments/{eid}`; both answer with the `Environment`. */
export interface EnvironmentIn {
  name: string;
  color?: TagColor | string;
}

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

export type ProviderType = string;

/**
 * Capability keys served by GET /api/providers/types. Keeping the union here
 * lets `lib/providers.ts` and the reverse-proxy grouping stay exhaustive when the
 * backend adds a key (certificates arrived with the Zoraxy provider).
 */
export type ProviderCapability =
  | 'proxy'
  | 'dns'
  | 'public_dns'
  | 'supports_auto_public_target'
  | 'supports_tunnel'
  | 'certificates';

export interface Provider {
  id: number;
  name: string;
  type: ProviderType;
  url: string;
  username: string;
  enabled: boolean | number;
  extra: Record<string, unknown>;
  created_at: string;
  status?: string;
  error_message?: string;
  capabilities?: string[];
}

/** One field of a guided setup step (`GET /api/providers/types` → `guided_steps[].fields[]`). */
export interface GuidedStepField {
  /** Provider column or `extra` key the value is stored under (`url`, `username`, `password`, `zone_id`…). */
  key: string;
  label: string;
  placeholder?: string;
  hint?: string;
  input_type?: 'url' | 'text' | 'password';
  optional?: boolean;
}

/** One step of the guided provider setup shipped with each provider type. */
export interface GuidedStep {
  title: string;
  body: string;
  fields?: GuidedStepField[];
}

/**
 * One entry of `GET /api/providers/types` (`PROVIDER_TYPES` in app/providers/factory.py).
 *
 * Every field is optional on purpose: the same shape describes a type this build knows and a
 * type a newer backend added, and a lookup that misses is filled with `{}` rather than left as
 * a hole. Ask `lib/providers.ts` about capabilities rather than reading `capabilities` or
 * `category` directly — the fallbacks for an older backend live there.
 *
 * This is the only declaration; `providerConstants.ts` re-exports it.
 */
export interface ProviderTypeMeta {
  label?: string;
  /** `proxy` or `dns` in practice; free text so a category a newer backend adds still parses. */
  category?: string;
  capabilities?: Partial<Record<ProviderCapability, boolean>>;
  requires_username?: boolean;
  requires_password?: boolean;
  /** False for a type listed but not shipped yet; the picker shows it disabled. */
  available?: boolean;
  description?: string;
  category_label?: string;
  category_color?: string;
  provider_color?: string;
  /** Logo key for `components/ui/ProviderLogos`. */
  icon?: string;
  color?: string;
  placeholder_url?: string;
  user_label?: string;
  pass_label?: string;
  user_placeholder?: string;
  /** Traefik: hosts can be listed but never written. */
  read_only?: boolean;
  guided_steps?: GuidedStep[];
  project_url?: string;
}

/**
 * `GET /api/providers/types` — keyed by provider type (`npm`, `zoraxy`, `cloudflare_tunnel`…).
 *
 * One cache entry (`['provider-types']`), one type: read it through `hooks/useProviderTypes.ts`
 * so the generic and the `queryFn` are written once.
 */
export type ProviderTypesResponse = Record<ProviderType, ProviderTypeMeta>;

/**
 * A provider's own `health_status()`. The generic shape is `{ok, status, error?}`;
 * Cloudflare Tunnel and Cloudflare DNS add the optional fields below.
 */
export interface ProviderHealthStatus {
  ok: boolean;
  /** `healthy` | `degraded` | `down` | `inactive` | `unknown` — free text from the provider. */
  status?: string;
  connections?: number;
  clients?: number;
  error?: string;
  /** Why a tunnel is degraded or inactive. */
  reason?: string;
  tunnel_id?: string;
  active_connections?: number;
  active_clients?: number;
  cloudflared_versions?: string[];
  declared_status?: string;
  /** Cloudflare DNS: zones the token can see. */
  zones_visible?: number;
}

/** One value of `GET /api/providers/health` — a bare connection test, not the full health dict. */
export interface ProviderHealthSummary {
  status: 'healthy' | 'unhealthy';
  error: string | null;
}

/** `GET /api/providers/health` — enabled providers only, keyed by provider id as a string. */
export type ProvidersHealthMap = Record<string, ProviderHealthSummary>;

/** `GET /api/providers/{pid}/health`. `provider` is the provider's name. */
export interface ProviderHealthDetail {
  provider: string;
  type: ProviderType;
  health: ProviderHealthStatus;
  ok: boolean;
}

export interface TunnelHealthItem {
  id: number;
  name: string;
  type?: ProviderType;
  enabled?: boolean | number;
  tunnel_id?: string;
  health?: ProviderHealthStatus;
}

/** `GET /api/providers/tunnels/health`. */
export interface TunnelHealthResponse {
  total?: number;
  healthy?: number;
  down?: number;
  items?: TunnelHealthItem[];
}

/** Body of `POST /api/providers`; answers with `ProviderCreated`. */
export interface ProviderIn {
  name: string;
  type: ProviderType;
  url: string;
  username: string;
  password: string;
  extra: Record<string, unknown>;
}

/** Body of `PUT /api/providers/{pid}` (every field optional; empty password keeps the stored one); answers `{ok}`. */
export interface ProviderUpdate extends Partial<ProviderIn> {
  enabled?: boolean | number;
}

export interface ProviderCreated {
  id: number;
  name: string;
  type: ProviderType;
}

/** A service still pointing at a provider about to be deleted (`DELETE /api/providers/{pid}` → 409 `detail.services[]`). */
export interface ProviderDependent {
  id: number;
  fqdn: string;
  /** `proxy`, `dns`, `tunnel`, or `extra proxy` / `extra dns` for push targets. */
  roles: string[];
  /**
   * Another provider still publishes this hostname once this one is gone. False means the
   * service keeps its public name and nothing serves it: that is the case worth warning
   * about, and the one the dialog used to claim for every dependent.
   */
  still_published?: boolean;
}

/** The 409 `detail` of `DELETE /api/providers/{pid}` when services depend on it and `?force=` was not set. */
export interface ProviderDeleteConflict {
  message: string;
  services: ProviderDependent[];
}

/** `DELETE /api/providers/{pid}?force=true`. */
export interface ProviderDeleteResult {
  /** False when `withdraw=true` was asked for and at least one record could not be taken off. */
  ok: boolean;
  unlinked_services: number[];
  /** `withdraw=true` was honoured: the records were taken off the provider before it went. */
  withdrawn?: boolean;
  /** One `fqdn: reason` per record the withdrawal could not remove; it is still live there. */
  errors?: string[];
}

/** Body of `POST /api/providers/{pid}/validate`. */
export interface ProviderValidateRequest {
  /** A hostname the write probe may create and delete (never an existing one). */
  hostname_hint?: string;
  write_probe?: boolean;
}

/** One host of `GET /api/providers/{pid}/proxy-hosts`. NPM ids are numbers; Zoraxy and Traefik key on the hostname. */
export interface ProxyHost {
  id: number | string;
  domains: string[];
  /** `scheme://host:port`, what the proxy forwards to. */
  target: string;
  scheme: string;
  host: string;
  port: number;
  ssl: boolean;
  websocket: boolean;
  cert_id: number | string | null;
  enabled?: boolean;
  /** Traefik middlewares attached to the router. */
  middlewares?: string[];
  tls_resolver?: string;
  bypass_global_tls?: boolean;
  tags?: string[];
}

/** `GET /api/providers/{pid}/proxy-hosts`. `provider` is the provider's name. */
export interface ProxyHostsResponse {
  provider: string;
  hosts: ProxyHost[];
}

/** Body of `POST /api/providers/{pid}/proxy-hosts` (`ProxyHostIn`); answers `{ok, result}`. */
export interface ProxyHostIn {
  domain_names: string[];
  forward_host: string;
  forward_port?: number;
  scheme?: ForwardScheme;
}

export interface ProxyHostCreateResult {
  ok: boolean;
  result: unknown;
}

/** One record of `GET /api/providers/{pid}/dns-records`. `type` and `proxied` come from Cloudflare only. */
export interface DnsRecord {
  domain: string;
  answer: string;
  /** `A`, `AAAA` or `CNAME` when the provider reports it. */
  type?: string;
  proxied?: boolean;
}

/** `GET /api/providers/{pid}/dns-records`. `provider` is the provider's name. */
export interface DnsRecordsResponse {
  provider: string;
  records: DnsRecord[];
}

/** Body of `POST /api/providers/{pid}/dns-records`; answers `{ok, domain, answer}`. Delete with `DELETE …/dns-records/{domain}?answer=`. */
export interface DnsRecordIn {
  domain: string;
  answer: string;
}

export interface DnsRecordCreateResult {
  ok: boolean;
  domain: string;
  answer: string;
}

// ---------------------------------------------------------------------------
// Services
// ---------------------------------------------------------------------------

export interface Service {
  id: number;
  subdomain: string;
  domain: string;
  target_ip: string;
  target_port: number;
  forward_scheme: ForwardScheme;
  websocket: boolean | number;
  expose_mode: ExposeMode;
  public_target_mode: PublicTargetMode;
  auto_update_dns: boolean | number;
  tunnel_hostname: string;
  dns_ip: string;
  /** NPM numbers its hosts; Zoraxy and Cloudflare Tunnel key them on the hostname. */
  npm_host_id: number | string | null;
  dns_provider_id: number | null;
  proxy_provider_id: number | null;
  tunnel_provider_id: number | null;
  enabled: boolean | number;
  status: ServiceStatus;
  last_checked: string | null;
  created_at: string;
  tags: Tag[];
  environments: Environment[];
  push_targets?: PushTarget[];
  // Denormalized provider names (populated by backend JOIN)
  proxy_provider_name?: string;
  dns_provider_name?: string;
  tunnel_provider_name?: string;
  dns_type?: string;
  proxy_type?: string;
  tunnel_type?: string;
  icon_url?: string;
  extra_proxy_provider_ids?: number[];
  extra_dns_provider_ids?: number[];
  public_host?: string;
}

export interface PushTarget {
  id?: number;
  provider_id: number;
  role: ProviderRole;
  provider_name?: string;
  provider_type?: string;
  provider_enabled?: boolean;
}

/** Body of `POST /api/services` and `PUT /api/services/{sid}` (`ServiceIn`, `extra="forbid"`: no other keys). */
export interface ServicePayload {
  subdomain: string;
  domain: string;
  target_ip: string;
  target_port: number;
  forward_scheme: ForwardScheme;
  websocket: boolean;
  expose_mode: ExposeMode;
  public_target_mode: PublicTargetMode;
  auto_update_dns: boolean;
  tunnel_hostname: string;
  dns_ip: string;
  dns_provider_id: number | null;
  proxy_provider_id: number | null;
  tunnel_provider_id: number | null;
  enabled: boolean;
  tag_ids: number[];
  environment_ids: number[];
  icon_url: string;
  extra_proxy_provider_ids: number[];
  extra_dns_provider_ids: number[];
}

/**
 * Body of `POST /api/services/preflight` (`ServicePreflightIn`): the exact `ServicePayload`
 * plus `service_id` when editing, so the public-host conflict check ignores the service
 * itself. Unknown keys are rejected with 422.
 */
export interface PreflightRequest extends ServicePayload {
  service_id?: number | null;
}

/**
 * One preflight check. Known names: `public_host_conflict`, `target_reachable`,
 * `https_port_hint`, `tunnel_health` (data = the tunnel's health dict),
 * `provider_target_required`, `proxy_connection`, `dns_target_resolution`
 * (data = `{resolved_target, source}`), and one per provider role that was checked.
 */
export interface PreflightCheck {
  name: string;
  ok: boolean;
  /** A failed blocking check means the save would fail; a failed non-blocking one is a warning. */
  blocking: boolean;
  /** The English sentence. Kept as the fallback when `detail_key` is unknown to this build. */
  detail: string;
  /** Short code of that sentence: `expose.preflight.detail.<detail_key>`. */
  detail_key?: string;
  /** The values the sentence was built from, substituted into the translation. */
  detail_params?: Record<string, string | number>;
  data?: Record<string, unknown>;
}

export interface PreflightSummary {
  blocking_failures: number;
  warnings: number;
  total: number;
}

/** `POST /api/services/preflight`. */
export interface PreflightResult {
  ok: boolean;
  public_host: string;
  checks: PreflightCheck[];
  summary: PreflightSummary;
}

/** `POST /api/services/{sid}/check` — one on-demand health check. */
export interface ServiceCheckResult {
  id: number;
  status: ServiceStatus;
  /** Null when the target never answered. */
  latency_ms: number | null;
  dns_resolved: string[] | null;
}

/** One line of `CheckAllResult.results`: what the fleet probe measured for one service. */
export interface CheckAllEntry {
  id: number;
  status: ServiceStatus;
  /** Null when the target never answered. */
  latency_ms: number | null;
}

/** `POST /api/services/check-all`. */
export interface CheckAllResult {
  checked: number;
  ok: number;
  error: number;
  /**
   * One entry per service actually probed — so `results.length` is `checked` minus the
   * tunnel services, which are skipped. Absent on instances older than 1.5.0.
   */
  results?: CheckAllEntry[];
}

export type BulkAction = 'enable' | 'disable' | 'delete';

/** Body of `POST /api/services/bulk`. */
export interface BulkActionBody {
  ids: number[];
  action: BulkAction;
}

/** `POST /api/services/bulk`. */
export interface BulkActionResult {
  ok: boolean;
  affected: number;
  errors: string[];
}

/** One uptime event of the last 24 h. */
export interface ServiceHistoryPoint {
  status: ServiceStatus;
  created_at: string;
}

/** `GET /api/services/history` — keyed by service id as a string. */
export type ServiceHistoryResponse = Record<string, ServiceHistoryPoint[]>;

// ---------------------------------------------------------------------------
// Drift, push & reconcile
// ---------------------------------------------------------------------------

export interface DriftIssue {
  severity: 'error' | 'warn';
  type: string;
  provider: string;
  /** The English sentence. Kept as the fallback when `detail_key` is unknown to this build. */
  detail: string;
  /** Short code of that sentence: `services.drift.detail.<detail_key>`. */
  detail_key?: string;
  /** The values the sentence was built from, substituted into the translation. */
  detail_params?: Record<string, string | number>;
}

/** `GET /api/services/{sid}/drift`. */
export interface DriftResult {
  service_id: number;
  public_host: string;
  mode: string;
  ok: boolean;
  issues: DriftIssue[];
}

/** `POST /api/services/{sid}/reconcile`. */
export interface ReconcileResult {
  ok: boolean;
  before: DriftResult;
  push: Record<string, unknown>;
  after: DriftResult;
}

/** What the push would do on one proxy provider (`POST /api/services/{sid}/push/dry-run`). */
export interface PushPlanProxyAction {
  provider_id: number;
  provider_name: string;
  provider_type: ProviderType;
  action: 'update' | 'create' | 'skip_read_only';
  target_host: string;
  target_origin?: string;
}

/** What the push would do on one DNS provider. */
export interface PushPlanDnsAction {
  provider_id: number;
  provider_name: string;
  provider_type: ProviderType;
  action: 'upsert';
  domain: string;
  target: string;
}

/** A column the push would rewrite on the service itself (an auto-resolved DNS target, mostly). */
export interface PushPlanServiceUpdate {
  field: string;
  old: unknown;
  new: unknown;
  source: string;
}

/** `POST /api/services/{sid}/push/dry-run` — nothing is written. */
export interface DryRunPlan {
  service_id: number;
  mode: ExposeMode | string;
  public_host: string;
  proxy_actions: PushPlanProxyAction[];
  dns_actions: PushPlanDnsAction[];
  service_updates: PushPlanServiceUpdate[];
  warnings: string[];
  errors: string[];
  dns_target: string;
  dns_target_source: string;
  would_change: boolean;
  ok: boolean;
}

/** `POST /api/services/{sid}/push`. */
export interface PushResult {
  ok: boolean;
  errors: string[];
}

// ---------------------------------------------------------------------------
// Health & Stats
// ---------------------------------------------------------------------------

/** `GET /api/health`. `disk_usage` is a percentage. */
export interface HealthResponse {
  ok: boolean;
  db: boolean;
  latency_ms: number;
  disk_usage: number;
  version: string;
}

export interface StatsResponse {
  services: number;
  providers: number;
  logs: number;
}

/** `GET /api/stats`. */
export interface Stats extends StatsResponse {
  services_ok: number;
  services_error: number;
  tags: number;
}

// ---------------------------------------------------------------------------
// Logs
// ---------------------------------------------------------------------------

export interface LogEntry {
  id: number;
  level: LogLevel;
  message: string;
  created_at: string;
}

/** `GET /api/logs?page&per_page&level`. `per_page` is capped at 200. */
export interface LogsResponse {
  total: number;
  page: number;
  per_page: number;
  pages: number;
  items: LogEntry[];
}

export type LogsPage = LogsResponse;

export interface LogsQuery {
  page?: number;
  per_page?: number;
  /** Empty for every level. */
  level?: LogLevel | '';
}

// ---------------------------------------------------------------------------
// Domains & Settings
// ---------------------------------------------------------------------------

/** `GET /api/domains` — the names only. */
export type DomainsResponse = string[];

/** Body of `POST /api/domains`; answers `{name}`. Delete with `DELETE /api/domains/{name}`. */
export interface DomainIn {
  name: string;
}

/** `GET /api/settings` — every value is text; `webhook_url` comes back masked. */
export type SettingsMap = Record<string, string>;

/**
 * The keys `GET /api/settings` may hold. `POST /api/settings` accepts the same keys except
 * `schema_version` and `setup_completed`; values are strings even for numbers and booleans.
 */
export interface AppSettings {
  theme?: 'light' | 'dark';
  /** IANA zone (`UTC`, `Europe/Paris`); empty means the browser's zone. */
  timezone?: string;
  /** Minutes, 0–1440; 0 disables the checker. */
  check_interval?: string;
  webhook_url?: string;
  webhook_enabled?: string;
  public_target_sources?: string;
  public_target_timeout?: string;
  public_target_priority?: string;
  /** Days, 1–365. */
  log_retention_days?: string;
  /** Days, 1–365. */
  monitoring_retention_days?: string;
  /** `'true'` | `'false'`. */
  auto_reconcile_enabled?: string;
  /** Minutes, 0–1440. */
  auto_reconcile_interval?: string;
  /** Days, 1–90. */
  webhook_retry_retention_days?: string;
  schema_version?: string;
  setup_completed?: string;
}

/** `POST /api/settings`. A 400 with "Nothing was saved -- key: reason; …" means every key was rejected. */
export interface SettingsSaveResult {
  ok: boolean;
  saved: string[];
  ignored: string[];
}

// ---------------------------------------------------------------------------
// Webhooks & Alerts
// ---------------------------------------------------------------------------

export interface Webhook {
  id: number;
  name: string;
  /** Scheme only -- the API never returns the full Apprise URL, which is the credential. */
  url_masked: string;
  enabled: boolean | number;
  created_at: string;
  scope_type: 'all' | 'provider' | 'service';
  scope_ref_id: number | null;
  repeat_interval_minutes: number;
  alert_on_any_down: boolean | number;
  alert_on_any_up: boolean | number;
  alert_on_integration_down: boolean | number;
  alert_on_integration_up: boolean | number;
  min_down_minutes: number;
}

export interface ServiceAlert {
  id: number;
  service_id: number;
  webhook_id: number;
  on_up: boolean | number;
  on_down: boolean | number;
  min_down_minutes: number;
}

/** One row of `GET /api/services/{sid}/alerts` — the alert joined with its webhook. */
export interface ServiceAlertRow extends ServiceAlert {
  webhook_name: string;
  webhook_url_masked: string;
}

/** One alert to keep, for `POST /api/services/{sid}/alerts`. */
export interface ServiceAlertInput {
  webhook_id: number;
  on_up?: boolean;
  on_down?: boolean;
  min_down_minutes?: number;
}

/** Body of `POST /api/services/{sid}/alerts` — replaces every alert of the service; answers `{ok}`. */
export interface ServiceAlertsConfig {
  alerts: ServiceAlertInput[];
}

// ---------------------------------------------------------------------------
// Certificates
// ---------------------------------------------------------------------------

export interface Certificate {
  id: number;
  provider: string;
  domain_names: string[];
  expires_on: string;
  nice_name?: string;
  issuer?: string;
}

export interface CertificateExpiry {
  id: number;
  provider_id: number;
  provider_name: string;
  domain_names?: string[];
  domains?: string[];
  expires_on: string;
  days_remaining: number | null;
  expiring_soon: boolean;
  expired: boolean;
  expiry_date_raw?: string | null;
}

export interface CertificateExpiryResponse {
  certificates: CertificateExpiry[];
  total: number;
  expiring_soon_count: number;
  warn_threshold_days: number;
}

// ---------------------------------------------------------------------------
// Docker
// ---------------------------------------------------------------------------

export interface DockerEndpoint {
  id: number;
  name: string;
  docker_host: string;
  enabled: boolean | number;
  is_default: boolean | number;
  created_at: string;
}

export interface ContainerSuggestion {
  subdomain: string;
  target_port: number;
  forward_scheme: ForwardScheme;
  confidence: 'high' | 'medium' | 'low';
  source: 'traefik_label' | 'vauxtra_label' | 'port_heuristic' | 'none';
}

export interface DockerContainer {
  id: string;
  name: string;
  image: string;
  status: string;
  labels: Record<string, string>;
  ports: Array<{ private_port: number; public_port?: number; type: string }>;
  suggestion: ContainerSuggestion | null;
}

/** `POST /api/docker/import`. */
export interface DockerImportResult {
  imported: number;
  skipped: number;
  errors: string[];
}

// ---------------------------------------------------------------------------
// API Keys
// ---------------------------------------------------------------------------

export interface ApiKey {
  id: number;
  name: string;
  prefix: string;       // First 8 chars, safe to display
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
}

export interface ApiKeyCreate {
  name: string;
  scopes: string[];
}

export interface ApiKeyCreated extends ApiKey {
  key: string;          // Full key — shown only once at creation
}

// ---------------------------------------------------------------------------
// Sync / Import
// ---------------------------------------------------------------------------

export interface SyncProxyHost {
  subdomain?: string;
  domain?: string;
  domains?: string[];
  domain_names?: string[];
  forward_host?: string;
  forward_port?: number;
  host?: string;
  port?: number;
  scheme?: string;
  forward_scheme?: string;
  _provider_id?: number;
  _provider_name?: string;
  _provider_type?: string;
  _provider_readonly?: boolean;
  _already_imported?: boolean;
  [key: string]: unknown;
}

export interface SyncDnsRewrite {
  subdomain?: string;
  domain?: string;
  answer?: string;
  target?: string;
  _provider_id?: number;
  _provider_name?: string;
  _already_imported?: boolean;
  [key: string]: unknown;
}

/** `POST /api/services/sync` — what every enabled provider currently serves. */
export interface SyncResult {
  proxy_hosts?: SyncProxyHost[];
  dns_rewrites?: SyncDnsRewrite[];
  [key: string]: unknown;
}

/** `POST /api/services/import`. */
export interface ImportResult {
  imported: number;
  errors: string[];
}

// ---------------------------------------------------------------------------
// Templates
// ---------------------------------------------------------------------------

/**
 * Body of `POST /api/templates` and `PUT /api/templates/{tid}` (`TemplateIn`). Every field
 * but `name` has a server default; `name` ≤ 64 chars, unique (409 on a duplicate).
 */
export interface TemplateIn {
  name: string;
  description: string;
  forward_scheme: ForwardScheme;
  /** 1–65535, or null to leave the port to the service. */
  target_port: number | null;
  websocket: boolean;
  expose_mode: ExposeMode;
  proxy_provider_id: number | null;
  dns_provider_id: number | null;
  tunnel_provider_id: number | null;
  public_target_mode: PublicTargetMode;
  domain: string;
  dns_ip: string;
  tag_ids: number[];
  icon_url: string;
}

/** The smallest body `POST /api/templates` accepts. */
export type TemplateInput = Pick<TemplateIn, 'name'> & Partial<TemplateIn>;

/** One row of `GET /api/templates` (ordered by name) and `GET /api/templates/{tid}`. */
export interface Template extends Omit<TemplateIn, 'websocket'> {
  id: number;
  websocket: boolean | number;
  created_at: string;
}

/**
 * `GET /api/templates/{tid}/apply` — the service-form defaults a template yields, ready to
 * spread into a `ServicePayload` draft. `_template_id` / `_template_name` are for the
 * "from template X" hint and must be dropped before the service is saved.
 */
export interface TemplateApplyResult {
  forward_scheme: ForwardScheme;
  target_port: number | null;
  websocket: boolean;
  expose_mode: ExposeMode;
  proxy_provider_id: number | null;
  dns_provider_id: number | null;
  tunnel_provider_id: number | null;
  public_target_mode: PublicTargetMode;
  domain: string;
  dns_ip: string;
  tag_ids: number[];
  icon_url: string;
  _template_id: number;
  _template_name: string;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

/** `GET /api/auth/me`. */
export interface AuthStatus {
  authenticated: boolean;
  auth_required: boolean;
  setup_required: boolean;
  auth_mode?: 'password' | 'open';
  /** Which store decides a login. `environment` means `APP_PASSWORD` wins and nothing
   *  written through the interface would ever be read. */
  password_source?: 'environment' | 'database';
}

export type AuthMe = AuthStatus;

/** Body of `POST /api/auth/login`; answers `{ok}`. */
export interface LoginRequest {
  password: string;
}

/** Body of `POST /api/auth/setup-password`; answers `{ok}`. */
export interface SetupPasswordRequest {
  password: string;
}

/** Body of `POST /api/auth/change-password`; answers `{ok}`. */
export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}

// ---------------------------------------------------------------------------
// Backup & restore
// ---------------------------------------------------------------------------

/**
 * The file `GET /api/backup` downloads (admin). `POST /api/backup/secure` adds
 * `encryption_salt` and `encrypted_fields`, with provider passwords and webhook URLs
 * encrypted under the passphrase.
 */
export interface BackupExport {
  version: string;
  exported_at: string;
  secrets_included: boolean;
  providers: Array<Record<string, unknown>>;
  services: Array<Record<string, unknown>>;
  tags: Array<Record<string, unknown>>;
  service_tags: Array<Record<string, unknown>>;
  service_push_targets: Array<Record<string, unknown>>;
  environments: Array<Record<string, unknown>>;
  service_environments: Array<Record<string, unknown>>;
  domains: Array<Record<string, unknown>>;
  webhooks: Array<Record<string, unknown>>;
  service_alerts: Array<Record<string, unknown>>;
  settings: Array<{ key: string; value: string }>;
  docker_endpoints: Array<Record<string, unknown>>;
  api_keys: Array<Record<string, unknown>>;
  encryption_salt?: string;
  encrypted_fields?: string[];
}

/** Body of `POST /api/backup/secure`. */
export interface SecureBackupRequest {
  passphrase: string;
}

/** Body of `POST /api/restore` (admin). `passphrase` decrypts a secure backup. */
export interface RestoreRequest {
  backup: BackupExport | Record<string, unknown>;
  passphrase?: string;
}

/** `POST /api/restore`. `webhooks_needing_url` counts webhooks whose URL could not be restored and must be re-entered. */
export interface RestoreResult {
  ok: boolean;
  services: number;
  providers: number;
  webhooks_needing_url: number;
}

// ---------------------------------------------------------------------------
// Provider Validation
// ---------------------------------------------------------------------------

export interface ProviderValidationCheck {
  name?: string;
  ok?: boolean;
  /** The English sentence. Kept as the fallback when `detail_code` is unknown to this build. */
  detail?: string;
  /** Short code of that sentence: `providers.diag.detail.<detail_code>`. */
  detail_code?: string;
  /** The values the sentence was built from, substituted into the translation. */
  detail_params?: Record<string, string | number>;
  blocking?: boolean;
}

/** `POST /api/providers/{pid}/test` and `POST /api/providers/{pid}/validate`. */
export interface ProviderValidationResult {
  ok: boolean;
  validation?: {
    checks?: ProviderValidationCheck[];
    warnings?: string[];
  };
  health?: {
    ok?: boolean;
    status?: string;
    error?: string;
  };
}

// ---------------------------------------------------------------------------
// Axios Error Helper
// ---------------------------------------------------------------------------

/**
 * The simplest error body. `detail` may also be a pydantic list or an object
 * (`ProviderDeleteConflict`); use `lib/errors.ts#getErrorMessage` rather than reading it directly.
 */
export interface ApiErrorResponse {
  detail?: string;
  message?: string;
}
