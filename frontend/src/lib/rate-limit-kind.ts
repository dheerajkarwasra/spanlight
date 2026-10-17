import type { RateLimitKind } from "@/lib/api";

/**
 * Plain-language names for the rate-limit kinds (Figma "Provider path" copy). `gateway` is
 * Spanlight's own per-key limit, never the provider's.
 */
const RATE_LIMIT_KIND_LABELS: Record<RateLimitKind, string> = {
  requests: "Request limit",
  tokens: "Token limit",
  spend_cap: "Spend cap",
  acceleration: "Ramp-up limit",
  overloaded: "Overloaded",
  gateway: "Gateway key limit",
  unknown: "Unknown",
};

/** Every kind in the backend's order (`RATE_LIMIT_KIND_ORDER`), which also breaks count ties. */
export const RATE_LIMIT_KIND_ORDER = Object.keys(RATE_LIMIT_KIND_LABELS) as [
  RateLimitKind,
  ...RateLimitKind[],
];

export function rateLimitKindLabel(kind: RateLimitKind): string {
  return RATE_LIMIT_KIND_LABELS[kind];
}
