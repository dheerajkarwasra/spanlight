import type { IncidentRef, ProviderHealth, RateLimitKind } from "@/lib/api";
import { parseMoney } from "@/lib/format";
import { providerLabel } from "@/lib/provider-label";
import { RATE_LIMIT_KIND_ORDER } from "@/lib/rate-limit-kind";
import { RETRY_NO_KNOWN_COST_REASON, RETRY_NOT_COMPUTED_REASON } from "@/lib/retry-reasons";

import { isProvider429 } from "./error-series";

/** U+00A0, so "54.0 %" and "12 s" never break before the unit. */
const NBSP = " ";

/** "54.0 %" (a ratio of 0.54 at one place); null for an unknown ratio. */
export function formatRatio(ratio: number | null, digits: number): string | null {
  if (ratio === null || !Number.isFinite(ratio)) {
    return null;
  }
  return `${(ratio * 100).toFixed(digits)}${NBSP}%`;
}

/** "12 s", "1.3 s"; null when the provider sent no retry-after. */
export function formatRetryAfter(seconds: number | null): string | null {
  if (seconds === null || !Number.isFinite(seconds)) {
    return null;
  }
  const text = Number.isInteger(seconds) ? String(seconds) : seconds.toFixed(1);
  return `${text}${NBSP}s`;
}

/**
 * The card's "429s": the provider's own 429s (every kind but overloaded 5xx and the gateway key's
 * limit, which are not the provider refusing) divided by failed calls; null when the provider had
 * no failed calls.
 */
export function refusalShare(provider: ProviderHealth): { refusals: number; share: number | null } {
  const refusals = provider.rate_limit_kinds
    .filter((kind) => isProvider429(kind.kind))
    .reduce((sum, kind) => sum + kind.count, 0);
  return { refusals, share: provider.errors > 0 ? refusals / provider.errors : null };
}

const utcTime = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
});

/** "Incident ongoing" while one is open, else "Incident ended 02:05 UTC"; null without one. */
export function incidentBadge(incidents: readonly IncidentRef[]): string | null {
  if (incidents.length === 0) {
    return null;
  }
  if (incidents.some((incident) => incident.resolved_at === null)) {
    return "Incident ongoing";
  }
  const latest = Math.max(...incidents.map((incident) => Date.parse(incident.resolved_at ?? "")));
  return Number.isNaN(latest)
    ? "Incident ended"
    : `Incident ended ${utcTime.format(latest)}${NBSP}UTC`;
}

/** One row of the rate-limit kinds table. */
export interface KindRow {
  provider: string | null;
  kind: RateLimitKind;
  count: number;
  /** Of this provider's rows in the table (429s and overloaded 5xx). */
  share: number | null;
  withRetryAfter: number;
  retryShare: number | null;
}

/** Each provider's kinds, busiest first (ties in kind order), providers in the API's order. */
export function kindRows(providers: readonly ProviderHealth[]): KindRow[] {
  return providers.flatMap((provider) => {
    const total = provider.rate_limit_kinds.reduce((sum, kind) => sum + kind.count, 0);
    return [...provider.rate_limit_kinds]
      .filter((kind) => kind.count > 0)
      .sort(
        (a, b) =>
          b.count - a.count ||
          RATE_LIMIT_KIND_ORDER.indexOf(a.kind) - RATE_LIMIT_KIND_ORDER.indexOf(b.kind),
      )
      .map((kind) => ({
        provider: provider.provider,
        kind: kind.kind,
        count: kind.count,
        share: total > 0 ? kind.count / total : null,
        withRetryAfter: kind.with_retry_after,
        retryShare: kind.count > 0 ? kind.with_retry_after / kind.count : null,
      }));
  });
}

export interface RetrySummary {
  /** Retried calls in the window; null when a trace predates retry tracking. */
  calls: number | null;
  /** Their cost in US dollars; null when unknown (see `reason`). */
  cost: number | null;
  /** Why the cost is unknown, or null when it is known. */
  reason: string | null;
  /** Providers with retries, each with its cost (null when unknown). */
  breakdown: { label: string; cost: number | null }[];
}

/** The window's retries over every provider; one unknown makes the total unknown. */
export function retrySummary(providers: readonly ProviderHealth[]): RetrySummary {
  const notComputed = providers.some((provider) => provider.retry_calls === null);
  const costs = providers.map((provider) =>
    provider.retry_cost_unknown ? null : parseMoney(provider.retry_cost_usd),
  );
  const calls = notComputed
    ? null
    : providers.reduce((sum, provider) => sum + (provider.retry_calls ?? 0), 0);
  const known = costs.every((cost) => cost !== null);
  let reason: string | null = null;
  if (notComputed) {
    reason = RETRY_NOT_COMPUTED_REASON;
  } else if (!known) {
    reason = RETRY_NO_KNOWN_COST_REASON;
  }
  return {
    calls,
    cost: reason === null ? costs.reduce<number>((sum, cost) => sum + (cost ?? 0), 0) : null,
    reason,
    breakdown: providers.flatMap((provider, index) =>
      provider.retry_calls === 0
        ? []
        : [{ label: providerLabel(provider.provider), cost: costs[index] ?? null }],
    ),
  };
}

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;

/** "7 days", "36 hours": a window's length for the samples notice. */
export function windowLength(durationMs: number): string {
  if (durationMs % DAY_MS === 0) {
    const days = durationMs / DAY_MS;
    return days === 1 ? "1 day" : `${String(days)} days`;
  }
  const hours = Math.round(durationMs / HOUR_MS);
  return hours === 1 ? "1 hour" : `${String(hours)} hours`;
}
