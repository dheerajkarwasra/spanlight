/**
 * Service-tier normalization: the SDK's copy of the server's table
 * (`backend/app/pricing/tiers.py::normalize_tier`, as copied in the Python SDK's `_tiers.py`).
 * The copies are kept in step by `sdks/fixtures/service-tier-cases.json`, which every suite runs.
 * Only the OpenAI and Anthropic spellings matter here, because those are the providers wrapped.
 */

export type ServiceTier = "standard" | "batch" | "priority" | "flex";

/** The longest unknown tier spelling a span keeps in `usage.service_tier_raw`. */
export const MAX_RAW_TIER_LENGTH = 64;

const CANONICAL_TIERS: ReadonlySet<string> = new Set(["standard", "batch", "priority", "flex"]);

// Azure is not an OpenAI alias on the server (it bills its own rates), so it is not one here.
const CANONICAL_PROVIDER: ReadonlyMap<string, string> = new Map([
  ["anthropic", "anthropic"],
  ["claude", "anthropic"],
  ["openai", "openai"],
]);

// Maps, not object literals, so a spelling such as "constructor" never finds a prototype member.
const PROVIDER_SPELLINGS: ReadonlyMap<string, ReadonlyMap<string, ServiceTier>> = new Map([
  [
    "anthropic",
    new Map<string, ServiceTier>([
      ["priority_on_demand", "priority"],
      ["flex_discount", "flex"],
    ]),
  ],
  [
    "openai",
    new Map<string, ServiceTier>([
      ["default", "standard"],
      ["auto", "standard"],
      ["scale", "standard"],
      ["fast", "priority"],
    ]),
  ],
]);

/** The stored tier for a provider's `service_tier` value, or undefined when it is unknown. */
export function normalizeTier(provider: string | undefined, raw: string): ServiceTier | undefined {
  const cleaned = raw.trim().toLowerCase();
  if (!cleaned) return undefined;
  if (CANONICAL_TIERS.has(cleaned)) return cleaned as ServiceTier;
  const providerName = provider?.trim().toLowerCase();
  if (!providerName) return undefined;
  const canonical = CANONICAL_PROVIDER.get(providerName) ?? providerName;
  return PROVIDER_SPELLINGS.get(canonical)?.get(cleaned);
}
