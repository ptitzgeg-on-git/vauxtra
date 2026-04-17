// ---------------------------------------------------------------------------
// Vauxtra API types — keep in sync with the FastAPI Pydantic models
// ---------------------------------------------------------------------------

export type ForwardScheme = 'http' | 'https';
export type ExposeMode = 'proxy_dns' | 'tunnel';
export type PublicTargetMode = 'manual' | 'auto';
export type ServiceStatus = 'ok' | 'error' | 'unknown';
export type ProviderRole = 'proxy' | 'dns';
export type LogLevel = 'ok' | 'info' | 'warning' | 'error';

// ---------------------------------------------------------------------------
// Tags & Environments
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

export type ProviderType =
  | 'npm'
  | 'adguard'
  | 'pihole'
  | 'cloudflare'
  | 'cloudflare_tunnel'
  | 'traefik';

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

export interface ProviderTypeMeta {
  label: string;
  category: 'proxy' | 'dns';
  capabilities: string[];
  requires_username?: boolean;
  requires_password?: boolean;
}

export interface ProviderHealthStatus {
  ok: boolean;
  status?: string;
  connections?: number;
  clients?: number;
  error?: string;
}

export interface TunnelHealthItem {
  id: number;
  name: string;
  health?: ProviderHealthStatus;
}

export interface TunnelHealthResponse {
  total?: number;
  healthy?: number;
  down?: number;
  items?: TunnelHealthItem[];
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
  npm_host_id: number | null;
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

// ---------------------------------------------------------------------------
// Drift
// ---------------------------------------------------------------------------

export interface DriftIssue {
  severity: 'error' | 'warn';
  type: string;
  provider: string;
  detail: string;
}

export interface DriftResult {
  service_id: number;
  public_host: string;
  mode: string;
  ok: boolean;
  issues: DriftIssue[];
}

export interface ReconcileResult {
  ok: boolean;
  before: DriftResult;
  push: Record<string, unknown>;
  after: DriftResult;
}

// ---------------------------------------------------------------------------
// Health & Stats
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Logs
// ---------------------------------------------------------------------------

export interface LogEntry {
  id: number;
  level: LogLevel;
  message: string;
  created_at: string;
}

export interface LogsResponse {
  total: number;
  page: number;
  per_page: number;
  pages: number;
  items: LogEntry[];
}

// ---------------------------------------------------------------------------
// Domains & Settings
// ---------------------------------------------------------------------------

export interface Domain {
  name: string;
  created_at: string;
}

export interface AppSettings {
  theme?: 'light' | 'dark';
  check_interval?: string;
  webhook_url?: string;
  webhook_enabled?: string;
  public_target_sources?: string;
  public_target_timeout?: string;
  public_target_priority?: string;
  schema_version?: string;
}

// ---------------------------------------------------------------------------
// Webhooks & Alerts
// ---------------------------------------------------------------------------

export interface Webhook {
  id: number;
  name: string;
  url: string;
  enabled: boolean | number;
  created_at: string;
}

export interface ServiceAlert {
  id: number;
  service_id: number;
  webhook_id: number;
  on_up: boolean | number;
  on_down: boolean | number;
  min_down_minutes: number;
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
  domain_names: string[];
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
  _provider_name?: string;
  _provider_type?: string;
  _already_imported?: boolean;
  [key: string]: unknown;
}

export interface SyncDnsRewrite {
  subdomain?: string;
  domain?: string;
  answer?: string;
  target?: string;
  _provider_name?: string;
  [key: string]: unknown;
}

export interface SyncResult {
  proxy_hosts?: SyncProxyHost[];
  dns_rewrites?: SyncDnsRewrite[];
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Provider Validation
// ---------------------------------------------------------------------------

export interface ProviderValidationCheck {
  name?: string;
  ok?: boolean;
  detail?: string;
  blocking?: boolean;
}

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

export interface ApiErrorResponse {
  detail?: string;
  message?: string;
}
