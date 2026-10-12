import { Link } from "@tanstack/react-router";
import { ArrowRight, CircleDashed } from "lucide-react";
import type { ReactNode } from "react";

import { CostValue, UNPRICED_OR_NO_USAGE_REASON } from "@/components/cost-value";
import { ErrorState } from "@/components/error-state";
import { UnknownValue } from "@/components/unknown-value";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell";
import { formatDuration, formatInteger } from "@/lib/format";

import { CardTitle } from "./card-title";
import { useVersionUsageQuery } from "./prompt-queries";

const NO_CALLS = "No calls used this version in the last 7 days.";
const NO_DURATIONS = "Unknown: the calls of this version reported no duration.";

interface UsageCardProps {
  name: string;
  version: number;
  /** The version's id, for the link to its traces; null until it is known. */
  versionId: string | null;
}

/** Figma "Usage · v3": seven days of traffic on one version, from the raw spans. */
export function UsageCard({ name, version, versionId }: UsageCardProps) {
  const usage = useVersionUsageQuery(name, version);
  return (
    <Card className="flex flex-col gap-4 p-5 sm:p-6" aria-busy={usage.isPending}>
      <CardTitle
        title={`Usage · v${String(version)}`}
        subtitle="Last 7 days"
        actions={versionId === null ? null : <ViewTracesLink versionId={versionId} />}
      />
      {usage.isPending ? (
        <div className="grid grid-cols-2 gap-2.5" role="status" aria-label="Loading usage">
          {Array.from({ length: 4 }, (_, index) => (
            <Skeleton key={index} className="h-[75px] rounded-tile" />
          ))}
        </div>
      ) : usage.isError ? (
        <div className="rounded-tile bg-surface-muted">
          <ErrorState
            compact
            error={usage.error}
            title="Couldn't load usage"
            onRetry={() => void usage.refetch()}
          />
        </div>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-2.5">
            <Metric label="Traces">{formatInteger(usage.data.traces)}</Metric>
            <Metric label="LLM calls">{formatInteger(usage.data.llm_calls)}</Metric>
            <Metric label="Cost">
              <CostValue
                cost={usage.data.cost_usd}
                hasUnpriced={usage.data.has_unpriced}
                unknownReason={usage.data.llm_calls === 0 ? NO_CALLS : UNPRICED_OR_NO_USAGE_REASON}
              />
            </Metric>
            <Metric label="p95 latency">
              {usage.data.p95_ms === null ? (
                <UnknownValue reason={usage.data.llm_calls === 0 ? NO_CALLS : NO_DURATIONS} />
              ) : (
                formatDuration(usage.data.p95_ms)
              )}
            </Metric>
          </dl>
          {usage.data.has_unpriced && usage.data.cost_usd !== null ? (
            <p className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <CircleDashed aria-hidden className="size-3.5 shrink-0 text-warning" />
              Some calls are unpriced, so the cost is a lower bound.
            </p>
          ) : null}
        </>
      )}
    </Card>
  );
}

/** The traces this version produced over the same seven days, in the explorer. */
function ViewTracesLink({ versionId }: { versionId: string }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Link
      to="/$orgId/$projectId/traces"
      params={{ orgId, projectId }}
      search={{ prompt_version: versionId, range: "7d" }}
      className="inline-flex min-h-6 items-center gap-1 rounded-sm text-[13px] font-semibold text-accent hover:underline"
    >
      View traces
      <ArrowRight aria-hidden className="size-3.5" />
    </Link>
  );
}

function Metric({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-tile bg-surface-muted p-3.5">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="text-[22px] leading-tight font-extrabold tracking-[-0.02em] text-foreground tabular">
        {children}
      </dd>
    </div>
  );
}
