import type { ReactNode } from "react";

import { CostValue, UNPRICED_OR_NO_USAGE_REASON } from "@/components/cost-value";
import { UnknownValue } from "@/components/unknown-value";
import type { PlaygroundResult } from "@/lib/api";
import { formatDuration, formatInteger } from "@/lib/format";

export const NO_LATENCY_REASON = "Unknown: the call did not report a duration.";
export const NOT_MEASURED_REASON = "Not measured: playground responses are not streamed.";

function Metric({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-overline text-subtle-foreground uppercase">{label}</dt>
      <dd className="text-label font-semibold text-foreground tabular">{children}</dd>
    </div>
  );
}

function tokensOf(result: PlaygroundResult): string | null {
  const usage = result.usage;
  if (!usage) {
    return null;
  }
  return `${formatInteger(usage.input_tokens) ?? "?"} in · ${formatInteger(usage.output_tokens) ?? "?"} out`;
}

/** Tokens, cost, latency and first token. Unknown ones are a dash with the reason; never 0. */
export function ResultMetrics({ result }: { result: PlaygroundResult }) {
  const tokens = tokensOf(result);
  const latency = formatDuration(result.latency_ms);
  return (
    <dl className="flex flex-wrap gap-x-4 gap-y-2">
      <Metric label="Tokens">
        {tokens ?? <UnknownValue reason={UNPRICED_OR_NO_USAGE_REASON} />}
      </Metric>
      <Metric label="Cost">
        <CostValue cost={result.cost_usd} unknownReason={UNPRICED_OR_NO_USAGE_REASON} />
      </Metric>
      <Metric label="Latency">{latency ?? <UnknownValue reason={NO_LATENCY_REASON} />}</Metric>
      <Metric label="First token">
        <UnknownValue reason={NOT_MEASURED_REASON} />
      </Metric>
    </dl>
  );
}
