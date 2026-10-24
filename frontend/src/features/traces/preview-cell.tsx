import { UnknownValue } from "@/components/unknown-value";
import { Skeleton } from "@/components/ui/skeleton";
import type { TraceSummary } from "@/lib/api";

import { TraceErrorLine } from "./trace-cells";

export const NO_INPUT_REASON = "No input captured on this trace";
const NO_OUTPUT_REASON = "No output captured on this trace";

/** One line of preview text, the whole preview in its title; "—" with a reason when there is none. */
function PreviewText({ text, reason, pending }: PreviewTextProps) {
  // Kept rows (keepPreviousData) may come from a request without previews, whose null means
  // "not asked", not "not captured".
  if (pending) {
    return <PreviewPending />;
  }
  if (!text) {
    return <UnknownValue reason={reason} />;
  }
  return (
    <span title={text} className="truncate text-muted-foreground">
      {text}
    </span>
  );
}

interface PreviewTextProps {
  text: string | null | undefined;
  reason: string;
  pending: boolean;
}

interface PreviewCellProps {
  trace: TraceSummary;
  /** The rows are kept from an earlier request until the new one lands. */
  pending: boolean;
}

export function InputPreviewCell({ trace, pending }: PreviewCellProps) {
  return <PreviewText text={trace.input_preview} reason={NO_INPUT_REASON} pending={pending} />;
}

/** A failed trace shows its error line here instead of the output (spec T2). */
export function OutputPreviewCell({ trace, pending }: PreviewCellProps) {
  if (trace.error_class !== null || trace.error_message) {
    return <TraceErrorLine errorClass={trace.error_class} message={trace.error_message} />;
  }
  return <PreviewText text={trace.output_preview} reason={NO_OUTPUT_REASON} pending={pending} />;
}

/** The preview line while the previews are on their way. */
export function PreviewPending() {
  return <Skeleton className="inline-block h-3 w-24 align-middle" />;
}
