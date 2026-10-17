import type { ReactNode } from "react";

import { CostValue, UNPRICED_OR_NO_USAGE_REASON } from "@/components/cost-value";
import { UnknownValue } from "@/components/unknown-value";
import type { PlaygroundResult, PlaygroundUsage } from "@/lib/api";
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

export const NO_OUTPUT_COUNT_REASON = "The provider never reported the output token count.";

function Tokens({ usage }: { usage: PlaygroundUsage | null }) {
  if (!usage) {
    return <UnknownValue reason={UNPRICED_OR_NO_USAGE_REASON} />;
  }
  const output = formatInteger(usage.output_tokens);
  return (
    <>
      {`${formatInteger(usage.input_tokens) ?? usage.input_tokens} in · `}
      {output ?? <UnknownValue reason={NO_OUTPUT_COUNT_REASON} />}
      {" out"}
    </>
  );
}

interface ResultMetricsProps {
  result: PlaygroundResult;
  /** Why the first token is not measured; the replay column says replays are not streamed. */
  notMeasuredReason?: string;
}

/** Tokens, cost, latency and first token. Unknown ones are a dash with the reason; never 0. */
export function ResultMetrics({
  result,
  notMeasuredReason = NOT_MEASURED_REASON,
}: ResultMetricsProps) {
  const latency = formatDuration(result.latency_ms);
  return (
    <dl className="flex flex-wrap gap-x-4 gap-y-2">
      <Metric label="Tokens">
        <Tokens usage={result.usage} />
      </Metric>
      <Metric label="Cost">
        <CostValue cost={result.cost_usd} unknownReason={UNPRICED_OR_NO_USAGE_REASON} />
      </Metric>
      <Metric label="Latency">{latency ?? <UnknownValue reason={NO_LATENCY_REASON} />}</Metric>
      <Metric label="First token">
        <UnknownValue reason={notMeasuredReason} />
      </Metric>
    </dl>
  );
}
