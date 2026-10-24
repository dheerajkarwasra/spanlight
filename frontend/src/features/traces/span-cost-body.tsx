import { CircleCheck } from "lucide-react";

import type { CostExplanation, Span } from "@/lib/api";

import {
  outputBreakdownNote,
  recordedUsageSentence,
  statusCopy,
  tierNoteCopy,
  visibleLines,
} from "./cost-explanation";
import { KnownUsage, OriginalUsageFields, PriceFooter } from "./span-cost-details";
import { CostLinesTable, CostTotals } from "./span-cost-lines";
import { Explanation, MatchLine, MismatchCallout, NoPriceLinks, Note } from "./span-cost-status";

const UNREPORTED_NOTE =
  "The gateway or SDK sets usage.unreported_reason when a stream ends before the final usage message. Nothing is estimated.";

interface SpanCostBodyProps {
  explanation: CostExplanation;
  span: Span;
}

/**
 * The expanded card for one loaded explanation (Figma "Span cost card / expanded ok", "/ mismatch",
 * "/ no usage", "/ no price", "/ override deleted", "/ cache hit", "/ tier …").
 */
export function SpanCostBody({ explanation, span }: SpanCostBodyProps) {
  const { status } = explanation;
  // A cache hit's lines are all zero (or absent without a price row): the sentence says it all.
  const lines = status === "cache_hit" ? [] : visibleLines(explanation.lines);
  const tierNote = tierNoteCopy(
    explanation.tier_note,
    explanation.provider_canonical,
    explanation.service_tier,
  );
  const breakdown = outputBreakdownNote(
    explanation.output_breakdown,
    explanation.usage.output_tokens,
  );
  return (
    <>
      <StatusSection explanation={explanation} span={span} />
      {tierNote !== null ? <Note tile>{tierNote}</Note> : null}
      {lines.length > 0 ? (
        <div className="flex flex-col gap-2.5">
          <CostLinesTable lines={lines} />
          <CostTotals recomputed={explanation.recomputed_usd} stored={explanation.stored_usd} />
        </div>
      ) : null}
      {breakdown !== null && lines.length > 0 ? <Note>{breakdown}</Note> : null}
      {status === "no_usage" ? <KnownUsage usage={explanation.usage} /> : null}
      {status === "no_usage" && explanation.usage.unreported_reason !== null ? (
        <Note>{UNREPORTED_NOTE}</Note>
      ) : null}
      {status === "cache_hit" && explanation.original_usage !== null ? (
        <OriginalUsageFields usage={explanation.original_usage} />
      ) : null}
      <PriceFooter explanation={explanation} span={span} />
    </>
  );
}

/** The line under the header: the match, the mismatch banner, or the server's sentence. */
function StatusSection({ explanation, span }: SpanCostBodyProps) {
  const { status } = explanation;
  const copy = statusCopy(status, explanation.message);
  if (status === "ok") {
    return (
      <>
        <MatchLine version={explanation.price?.version ?? span.pricing_version} />
        {copy.body !== null ? <Explanation>{copy.body}</Explanation> : null}
      </>
    );
  }
  if (status === "mismatch") {
    return (
      <MismatchCallout
        recomputed={explanation.recomputed_usd}
        stored={explanation.stored_usd}
        spanId={span.span_id}
      />
    );
  }
  if (status === "no_price") {
    const usage = recordedUsageSentence(explanation.usage);
    return (
      <>
        <Explanation>{[copy.body, usage].filter(Boolean).join(" ")}</Explanation>
        <NoPriceLinks provider={span.provider} model={span.model} />
      </>
    );
  }
  if (copy.body === null) {
    return null;
  }
  return (
    <Explanation icon={status === "cache_hit" ? CircleCheck : undefined}>{copy.body}</Explanation>
  );
}
