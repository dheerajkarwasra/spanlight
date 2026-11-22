import { Sparkles } from "lucide-react";

import { UnknownValue } from "@/components/unknown-value";
import type { Explanation } from "@/lib/api";
import { formatCost } from "@/lib/format";

import { ADVISORY_LABEL } from "./explain-state";
import { compactRelative, formatDateTime } from "./insight-format";

/** Within a day the age reads "2 min ago"; older explanations show the date and time. */
function whenText(iso: string, now: Date): string {
  const relative = compactRelative(iso, now);
  if (relative !== null && !relative.endsWith(" d ago")) {
    return relative;
  }
  return formatDateTime(iso) ?? "Unknown time";
}

/** The label that sits above every stored explanation. */
export function AdvisoryLabel() {
  return (
    <p className="flex items-start gap-2 rounded-input bg-accent-subtle px-3.5 py-2.5 text-sm font-medium text-accent">
      <Sparkles aria-hidden className="mt-0.5 size-4 shrink-0" strokeWidth={2} />
      {ADVISORY_LABEL}
    </p>
  );
}

interface ExplanationViewProps {
  explanation: Explanation;
  now: Date;
}

/**
 * One stored explanation: the advisory label, the text, then model, cost and time. The text is
 * the provider's output and is rendered as plain text only: no HTML, no Markdown.
 */
export function ExplanationView({ explanation, now }: ExplanationViewProps) {
  const cost = formatCost(explanation.cost_usd);
  return (
    <article className="flex flex-col gap-3">
      <AdvisoryLabel />
      <p className="text-sm leading-relaxed [overflow-wrap:anywhere] whitespace-pre-line text-foreground">
        {explanation.text}
      </p>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span className="font-mono">{explanation.model}</span>
        <span aria-hidden>·</span>
        {cost === null ? (
          <UnknownValue reason="The call's cost is not known" />
        ) : (
          <span className="tabular">{cost}</span>
        )}
        <span aria-hidden>·</span>
        <time dateTime={explanation.created_at}>{whenText(explanation.created_at, now)}</time>
      </p>
    </article>
  );
}
