/**
 * Gateway types, mirroring the backend's request and response bodies exactly (snake_case, money
 * as a decimal string, every unknown value `null`). Re-exported through `types.ts`.
 */

import type { User } from "./types";

export type ProviderKind = "openai" | "anthropic" | "openai_compatible";

/* ---------- Credentials ---------- */

/** A stored provider credential. The key itself is write-only: no response ever carries it. */
export interface Credential {
  id: string;
  name: string;
  provider: ProviderKind;
  /** Only `openai_compatible` credentials have one. */
  base_url: string | null;
  created_by: User | null;
  created_at: string;
  last_used_at: string | null;
  last_checked_at: string | null;
  /** What the last check or call failed with; null when it worked or was never tried. */
  last_error: string | null;
  rotated_at: string | null;
}

export interface CredentialCreate {
  name: string;
  provider: ProviderKind;
  api_key: string;
  /** Required for `openai_compatible`, refused for the others. */
  base_url?: string | null;
}

export interface CredentialCheck {
  status: "ok" | "error";
  error: string | null;
  checked_at: string;
}

/* ---------- Price overrides ---------- */

/** An org's own price for a model, used instead of the shared table. USD per million tokens. */
export interface PriceOverride {
  id: string;
  provider: string;
  model_pattern: string;
  input_per_mtok: string;
  output_per_mtok: string;
  cached_input_per_mtok: string | null;
  /** Cache-write rates per TTL; null when writes are billed at the input rate. */
  cache_write_5m_per_mtok: string | null;
  cache_write_1h_per_mtok: string | null;
  /** The smallest prompt (in tokens) this override prices; 0 is the base tier. */
  min_prompt_tokens: number;
  effective_from: string;
  created_by: User | null;
  created_at: string;
}

export interface PriceOverrideCreate {
  provider: string;
  model_pattern: string;
  /** Decimal strings, below 1 000 000 with at most 6 decimals. */
  input_per_mtok: string;
  output_per_mtok: string;
  cached_input_per_mtok?: string | null;
  cache_write_5m_per_mtok?: string | null;
  cache_write_1h_per_mtok?: string | null;
  /** Integer >= 0; defaults to 0, the base tier. */
  min_prompt_tokens?: number;
  /** Defaults to now on the server. */
  effective_from?: string | null;
}

/* ---------- Routes ---------- */

/**
 * A failure after which the gateway tries the route's next target. `auth_error` (a 401 or 403) is
 * opt-in: the server's default lists the other four, and it needs two targets on different
 * credentials.
 */
export type FallbackCondition =
  "status_5xx" | "rate_limited" | "timeout" | "connection_error" | "auth_error";

export interface RouteTarget {
  credential_id: string;
  /** 1 to 100: the share of calls the target gets among targets of the same rank. */
  weight?: number;
  /** Requested model name to the provider's model name. */
  model_aliases?: Record<string, string>;
}

export interface RetryPolicy {
  /** 1 to 5, the first try included. */
  max_attempts?: number;
  backoff_ms?: number;
  max_backoff_ms?: number;
  honour_retry_after?: boolean;
  /** Provider statuses (400 to 599) that count as retryable. */
  on_statuses?: number[];
}

export interface FallbackPolicy {
  on?: FallbackCondition[];
}

export interface RouteConfig {
  targets: RouteTarget[];
  retry: RetryPolicy;
  fallback: FallbackPolicy;
  /** 1 000 to 600 000, the budget for every attempt together. */
  timeout_ms?: number;
}

export interface Route {
  id: string;
  name: string;
  is_default: boolean;
  /** Bumps on every save; sent back as `expected_version` to detect a concurrent edit. */
  version: number;
  config: RouteConfig;
  updated_at: string;
  updated_by: User | null;
}

export interface RouteCreate {
  name: string;
  config: RouteConfig;
}

export interface RouteUpdate {
  config: RouteConfig;
  expected_version: number;
}

export interface RouteVersion {
  version: number;
  config: RouteConfig;
  created_at: string;
  updated_by: User | null;
}

/* ---------- Gateway keys ---------- */

/** A key an app calls the gateway with. The secret is only in `CreatedGatewayKey`. */
export interface GatewayKey {
  id: string;
  name: string;
  prefix: string;
  environment: string;
  /** Null: the project's default route. */
  route_id: string | null;
  /** Null: no limit. */
  rpm_limit: number | null;
  tpm_limit: number | null;
  /** Null: caching off for this key. */
  cache_ttl_seconds: number | null;
  /** Empty: every model is allowed. */
  allowed_models: string[];
  default_tags: string[];
  fault_profile_id: string | null;
  created_by: User | null;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface CreatedGatewayKey extends GatewayKey {
  secret: string;
}

export interface GatewayKeyCreate {
  name: string;
  environment: string;
  route_id?: string | null;
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  cache_ttl_seconds?: number | null;
  allowed_models?: string[];
  default_tags?: string[];
}

/**
 * A partial edit: only the fields sent change. `rpm_limit`, `tpm_limit`, `cache_ttl_seconds` and
 * `fault_profile_id` are cleared by sending `null`.
 */
export interface GatewayKeyUpdate {
  name?: string | null;
  environment?: string | null;
  route_id?: string | null;
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  cache_ttl_seconds?: number | null;
  allowed_models?: string[] | null;
  default_tags?: string[] | null;
  fault_profile_id?: string | null;
}

/* ---------- Fault profiles (Integration Lab) ---------- */

export type FaultScenario =
  | "auth_expired"
  | "scope_denied"
  | "rate_limited"
  | "unsupported_parameter"
  | "provider_5xx"
  | "malformed_json"
  | "truncated_stream"
  | "slow_response"
  | "timeout";

/** The parameters of each scenario; every field has a server default when omitted. */
export interface FaultParamsByScenario {
  auth_expired: Record<string, never>;
  scope_denied: Record<string, never>;
  rate_limited: { retry_after_s?: number };
  unsupported_parameter: { param?: string };
  provider_5xx: { status?: 500 | 502 | 503 | 529 };
  malformed_json: { keep_fraction?: number };
  truncated_stream: { after_chunks?: number };
  slow_response: { delay_ms?: number };
  timeout: { hold_ms?: number };
}

export type FaultParams = FaultParamsByScenario[FaultScenario];

export interface FaultProfile {
  id: string;
  name: string;
  scenario: FaultScenario;
  params: FaultParams;
  /** 0 to 1: the share of calls the fault hits. */
  probability: number;
  enabled: boolean;
  expires_at: string | null;
  /** Enabled and not expired. */
  active: boolean;
  attached_key_ids: string[];
  created_by: User | null;
  created_at: string;
  updated_at: string;
}

export interface FaultProfileCreate {
  name: string;
  scenario: FaultScenario;
  /** Omitted: the scenario's defaults. */
  params?: FaultParams;
  probability?: number;
  enabled?: boolean;
  expires_at?: string | null;
}

/** `params` must be sent together with `scenario`. */
export interface FaultProfileUpdate {
  name?: string | null;
  scenario?: FaultScenario | null;
  params?: FaultParams | null;
  probability?: number | null;
  enabled?: boolean | null;
  expires_at?: string | null;
}

/* ---------- Overview ---------- */

export interface GatewayKeyUsage {
  key_id: string;
  name: string;
  environment: string;
  requests: number;
  errors: number;
  cost_usd: string | null;
}

export interface GatewayTargetUsage {
  credential_id: string | null;
  credential_name: string;
  provider: ProviderKind | null;
  requests: number;
  errors: number;
  p95_ms: number | null;
}

export interface GatewayCacheStats {
  hits: number;
  misses: number;
  /** Null when the cache saw no lookups. */
  hit_rate: number | null;
}

export interface FaultScenarioCount {
  scenario: FaultScenario;
  count: number;
}

/** Gateway traffic of a window of at most 7 days. */
export interface GatewayOverview {
  requests: number;
  /** Includes calls failed on purpose by Lab faults. */
  errors: number;
  error_rate: number | null;
  retries: number;
  fallbacks: number;
  p95_ttft_ms: number | null;
  p95_overhead_ms: number | null;
  cache: GatewayCacheStats;
  faults: FaultScenarioCount[];
  by_key: GatewayKeyUsage[];
  by_target: GatewayTargetUsage[];
}
