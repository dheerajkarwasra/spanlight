import { Link } from "@tanstack/react-router";
import { Info } from "lucide-react";

import { ApproxValue } from "@/components/approx-value";
import { NO_PRICE_REASON } from "@/components/cost-value";
import { Tooltip } from "@/components/ui/tooltip";
import { UNKNOWN_DASH_CLASS, UnknownValue } from "@/components/unknown-value";
import { useProjectParams } from "@/features/shell/project-context";
import type { ModelMetrics } from "@/lib/api";
import { formatCost, formatDuration } from "@/lib/format";
import { NO_PROVIDER_REASON } from "@/lib/provider-label";
import { cn } from "@/lib/utils";

import { isUnpriced } from "./models";

const NO_CALLS = "No LLM calls in this period";

/** A p50 or p95 in milliseconds; "≈" when the window reads hourly rollups. */
export function ModelLatency({
  value,
  approximate,
}: {
  value: number | null;
  approximate: boolean;
}) {
  return (
    <ApproxValue value={formatDuration(value)} approximate={approximate} unknownReason={NO_CALLS} />
  );
}

export function ModelCost({ model }: { model: ModelMetrics }) {
  if (isUnpriced(model)) {
    return (
      <span className="inline-flex items-center gap-1.5 text-subtle-foreground">
        <Info aria-hidden className="size-3.5" />
        <UnknownValue reason={NO_PRICE_REASON} />
      </span>
    );
  }
  return <>{formatCost(model.cost_usd)}</>;
}

export function ModelName({ model }: { model: string | null }) {
  const { orgId, projectId } = useProjectParams();
  if (model === null) {
    return <span className="text-sm text-muted-foreground italic">Unknown model</span>;
  }
  return (
    <Link
      to="/$orgId/$projectId/traces"
      params={{ orgId, projectId }}
      search={{ model }}
      title={`View traces using ${model}`}
      className="rounded-sm font-mono text-label text-foreground hover:text-accent hover:underline hover:underline-offset-4"
    >
      {model}
    </Link>
  );
}

/** Cost › Pricing, where an unpriced model gets a price; follows an unpriced note. */
export function SetPricesLink() {
  const { orgId, projectId } = useProjectParams();
  return (
    <Link
      to="/$orgId/$projectId/pricing"
      params={{ orgId, projectId }}
      className="shrink-0 rounded-sm font-semibold text-accent hover:underline hover:underline-offset-4"
    >
      Set prices
    </Link>
  );
}

/**
 * Marks a row with no provider, so a model's gateway cache hits and refusals don't read as a
 * duplicate of its provider row.
 */
export function NoProviderTag({ className }: { className?: string }) {
  return (
    <Tooltip content={NO_PROVIDER_REASON}>
      <span
        tabIndex={0}
        aria-label={`No provider: ${NO_PROVIDER_REASON}`}
        className={cn(
          "shrink-0 cursor-help rounded-sm text-xs font-medium",
          UNKNOWN_DASH_CLASS,
          className,
        )}
      >
        No provider
      </span>
    </Tooltip>
  );
}
