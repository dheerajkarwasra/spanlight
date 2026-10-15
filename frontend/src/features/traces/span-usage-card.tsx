import { NO_PRICE_REASON } from "@/components/cost-value";
import { UnknownValue, ValueOrUnknown } from "@/components/unknown-value";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { Span } from "@/lib/api";
import { formatCost, formatDuration, formatInteger } from "@/lib/format";

import { MetaField } from "./meta-field";

const NOT_REPORTED = "Not reported by the SDK";

/** True when the span carries any model-call data worth a usage grid. */
function hasUsage(span: Span): boolean {
  return (
    span.model !== null ||
    span.provider !== null ||
    span.input_tokens !== null ||
    span.output_tokens !== null ||
    span.cached_tokens !== null ||
    span.cache_write_tokens !== null ||
    span.cost_usd !== null
  );
}

/** Figma "Usage": model, tokens and cost of the selected span. */
export function SpanUsageCard({ span }: { span: Span }) {
  return (
    <Card className="flex flex-col gap-3.5 p-5">
      <div className="flex min-w-0 items-baseline gap-2">
        <h2 className="shrink-0 text-card text-foreground">Usage</h2>
        <span title={span.name} className="truncate font-mono text-label text-muted-foreground">
          {span.name}
        </span>
      </div>
      {hasUsage(span) ? (
        <UsageGrid span={span} />
      ) : (
        <p className="text-sm text-muted-foreground">
          This span made no model call. Select an LLM span to see its tokens and cost.
        </p>
      )}
    </Card>
  );
}

function UsageGrid({ span }: { span: Span }) {
  const cost = formatCost(span.cost_usd);
  const costReason = span.model === null ? "No model call on this span" : NO_PRICE_REASON;
  return (
    <dl className="grid grid-cols-2 gap-x-3 gap-y-3.5 sm:grid-cols-4">
      <MetaField layout="stacked" label="Model" mono={span.model !== null}>
        <ValueOrUnknown value={span.model} reason={NOT_REPORTED} />
      </MetaField>
      <MetaField layout="stacked" label="Provider">
        <ValueOrUnknown value={span.provider} reason={NOT_REPORTED} />
      </MetaField>
      <MetaField layout="stacked" label="Input tokens">
        <TokenCount value={span.input_tokens} />
      </MetaField>
      <MetaField layout="stacked" label="Output tokens">
        <TokenCount value={span.output_tokens} />
      </MetaField>
      <MetaField layout="stacked" label="Cached tokens">
        <TokenCount value={span.cached_tokens} />
      </MetaField>
      {span.cache_write_tokens !== null ? (
        <MetaField layout="stacked" label="Cache writes" wrap>
          <TokenCount value={span.cache_write_tokens} />
          <CacheWrite1hSuffix tokens={span.cache_write_1h_tokens} />
        </MetaField>
      ) : null}
      <MetaField layout="stacked" label="Cost">
        {cost === null ? (
          <UnknownValue reason={costReason} />
        ) : (
          <span className="tabular">{cost}</span>
        )}
      </MetaField>
      <MetaField layout="stacked" label="Time to first token">
        <ValueOrUnknown
          value={formatDuration(span.time_to_first_token_ms)}
          reason="Not streamed or not reported"
          className="tabular"
        />
      </MetaField>
      <MetaField layout="stacked" label="Pricing version" mono={span.pricing_version !== null}>
        <ValueOrUnknown value={span.pricing_version} reason="No price was applied" />
      </MetaField>
    </dl>
  );
}

function TokenCount({ value }: { value: number | null }) {
  return <ValueOrUnknown value={formatInteger(value)} reason={NOT_REPORTED} className="tabular" />;
}

/** Figma "Span usage card · cache writes": the muted "· 1 h: N" part of the cache-write count. */
function CacheWrite1hSuffix({ tokens }: { tokens: number | null }) {
  const count = tokens !== null && tokens > 0 ? formatInteger(tokens) : null;
  if (count === null) {
    return null;
  }
  return (
    <span className="ml-1 font-normal text-muted-foreground tabular">
      <span aria-hidden>{`· 1 h: ${count}`}</span>
      <span className="sr-only">{`, 1-hour cache: ${count}`}</span>
    </span>
  );
}

export function SpanUsageSkeleton() {
  return (
    <Card aria-hidden className="flex flex-col gap-3.5 p-5">
      <Skeleton className="h-5 w-40" />
      <div className="grid grid-cols-2 gap-x-3 gap-y-3.5 sm:grid-cols-4">
        {Array.from({ length: 8 }, (_, index) => (
          <div key={index} className="flex flex-col gap-1.5">
            <Skeleton className="h-3 w-16" />
            <Skeleton className="h-4 w-20" />
          </div>
        ))}
      </div>
    </Card>
  );
}
