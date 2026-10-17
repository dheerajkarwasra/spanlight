import { useId, type ReactNode } from "react";

import { UnknownValue } from "@/components/unknown-value";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import type { ProviderHealth } from "@/lib/api";
import { formatInteger, parseMoney, pluralize } from "@/lib/format";
import { providerLabel } from "@/lib/provider-label";
import { rateLimitKindLabel } from "@/lib/rate-limit-kind";

import { dominantKind } from "./error-series";
import { formatRatio, incidentBadge, refusalShare } from "./providers-format";

/**
 * Figma "Provider card": calls, error rate, the provider 429 share of failed calls (overloaded 5xx
 * and the gateway key's limit excluded) and the kind most of them were, with a badge when a status
 * page incident overlapped.
 */
export function ProviderCard({ provider }: { provider: ProviderHealth }) {
  const titleId = useId();
  const name = providerLabel(provider.provider);
  const badge = incidentBadge(provider.incidents);
  const { refusals, share } = refusalShare(provider);
  const errorRate = parseMoney(provider.error_rate);
  const kind = dominantKind(provider.rate_limit_kinds);
  const kindCount = provider.rate_limit_kinds.find((entry) => entry.kind === kind)?.count ?? 0;

  return (
    <Card role="region" aria-labelledby={titleId} className="flex min-w-0 flex-col gap-4 p-5">
      <div className="flex items-center gap-2">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <h2 id={titleId} className="truncate text-card">
            {name}
          </h2>
          {provider.provider !== null ? (
            <p className="truncate font-mono text-[13px] text-muted-foreground">
              {provider.provider}
            </p>
          ) : null}
        </div>
        {badge ? <Badge variant="warning">{badge}</Badge> : null}
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-3">
        <Stat label="LLM calls" detail="in the window">
          {formatInteger(provider.llm_calls)}
        </Stat>
        <Stat label="Error rate" detail={pluralize(provider.errors, "failed call")}>
          {errorRate === null ? (
            <UnknownValue reason="No LLM calls from this provider in the window" />
          ) : (
            formatRatio(errorRate, 2)
          )}
        </Stat>
        <Stat
          label="429s"
          detail={
            share === null
              ? "no failed calls"
              : `${formatInteger(refusals) ?? "0"} of ${pluralize(provider.errors, "failed call")}`
          }
        >
          {share === null ? (
            <UnknownValue reason="No failed calls to divide by in the window" />
          ) : (
            formatRatio(share, 1)
          )}
        </Stat>
        <Stat
          label="Mostly"
          detail={
            kind === null
              ? "no provider 429s"
              : `${formatInteger(kindCount) ?? "0"} of ${pluralize(refusals, "refusal")}`
          }
        >
          {kind === null ? "None" : rateLimitKindLabel(kind)}
        </Stat>
      </dl>
    </Card>
  );
}

interface StatProps {
  label: string;
  detail: string;
  children: ReactNode;
}

function Stat({ label, detail, children }: StatProps) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="truncate text-h2 text-foreground tabular">{children}</dd>
      <dd className="text-xs font-medium text-muted-foreground">{detail}</dd>
    </div>
  );
}
