import { CostValue, UNPRICED_OR_NO_USAGE_REASON } from "@/components/cost-value";
import { UnknownValue } from "@/components/unknown-value";
import type { PromptVersionUsage } from "@/lib/api";
import { pluralize } from "@/lib/format";

/** A version row's "12 traces · $0.041" over the last seven days; null when it wasn't loaded. */
export function VersionUsageLine({ usage }: { usage: PromptVersionUsage | null }) {
  if (usage === null) {
    return <UnknownValue reason="Usage not loaded" />;
  }
  if (usage.traces === 0) {
    return <span>No calls in 7 days</span>;
  }
  return (
    <span>
      {pluralize(usage.traces, "trace")} ·{" "}
      <CostValue
        cost={usage.cost_usd}
        hasUnpriced={usage.has_unpriced}
        unknownReason={UNPRICED_OR_NO_USAGE_REASON}
      />
    </span>
  );
}
