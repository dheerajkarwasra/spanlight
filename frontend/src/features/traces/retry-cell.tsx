import { RefreshCw } from "lucide-react";

import { UnknownValue } from "@/components/unknown-value";
import { Tooltip } from "@/components/ui/tooltip";
import type { TraceSummary } from "@/lib/api";
import { RETRY_NOT_COMPUTED_REASON } from "@/lib/retry-reasons";

import {
  RETRY_LIST_EXPLANATION,
  formatRetries,
  retryBadgeText,
  retrySummary,
} from "./retry-format";

type RetryFields = Pick<TraceSummary, "retry_calls" | "retry_cost_usd">;

/**
 * Figma "Traces/List/Retry cost column", Retries: the count, with the retry cost in its tooltip.
 * A known zero is a plain "0"; a trace stored before the columns existed is "—" with its reason.
 */
export function RetryCountCell({ trace }: { trace: RetryFields }) {
  const calls = trace.retry_calls;
  if (calls === null) {
    return <UnknownValue reason={RETRY_NOT_COMPUTED_REASON} />;
  }
  if (calls === 0) {
    return <span className="text-muted-foreground tabular">0</span>;
  }
  const text = formatRetries(calls, trace.retry_cost_usd);
  const summary = retrySummary(calls, trace.retry_cost_usd) ?? "";
  const detail = text.reason ?? RETRY_LIST_EXPLANATION;
  return (
    <Tooltip
      content={
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold">{summary}</span>
          <span className="text-rail-muted-foreground">{detail}</span>
        </span>
      }
    >
      <button
        type="button"
        aria-label={`${summary}. ${detail}`}
        className="cursor-help rounded-sm font-semibold text-foreground tabular"
      >
        {text.count}
      </button>
    </Tooltip>
  );
}

/** Retry cost, shown only while the retry-cost sort or the has-retries filter is on. */
export function RetryCostCell({ trace }: { trace: RetryFields }) {
  const text = formatRetries(trace.retry_calls, trace.retry_cost_usd);
  if (text.reason !== null) {
    return <UnknownValue reason={text.reason} />;
  }
  return <span className="font-semibold text-foreground tabular">{text.cost}</span>;
}

/**
 * The phone tile's retries badge ("3 retries · $0.0291", "Retries —"). It sits inside the tile's
 * link, so it is a passive badge: the "—" forms are explained in the footnote under the list and
 * read out here, never in a nested tooltip control. Nothing renders for a trace without retries.
 */
export function RetryBadge({ trace }: { trace: RetryFields }) {
  const label = retryBadgeText(trace.retry_calls, trace.retry_cost_usd);
  if (label === null) {
    return null;
  }
  const reason = formatRetries(trace.retry_calls, trace.retry_cost_usd).reason;
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-surface-muted px-2 py-0.5 text-xs font-medium text-foreground tabular">
      <RefreshCw aria-hidden className="size-3" />
      {label}
      {reason !== null ? <span className="sr-only">: {reason}</span> : null}
    </span>
  );
}
