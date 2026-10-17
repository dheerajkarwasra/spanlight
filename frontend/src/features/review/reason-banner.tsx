import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { useProjectParams } from "@/features/shell";
import type { QueueItem, TraceDetail } from "@/lib/api";
import { cn } from "@/lib/utils";

import { ReasonChip } from "./reason-chip";
import { reasonSentence } from "./reason-copy";
import { REASON_STYLES } from "./reason-style";

interface ReasonBannerProps {
  item: QueueItem;
  /** Names the first failed span; null while the trace loads (the span id stands in). */
  trace: Pick<TraceDetail, "spans"> | null;
}

/**
 * Figma "Annotate / reason banner": one line above the trace saying why it is in the queue, with
 * the lower-priority reasons it matched too. A Doctor finding links to the insight.
 */
export function ReasonBanner({ item, trace }: ReasonBannerProps) {
  const reason = item.reason ?? "manual";
  const { icon: Icon, banner, iconColor } = REASON_STYLES[reason];
  const also = item.reason_detail?.also ?? [];
  return (
    <div
      role="note"
      aria-label="Why this trace"
      className={cn("flex flex-wrap items-start gap-x-4 gap-y-2 rounded-tile px-4 py-3", banner)}
    >
      <p className="flex min-w-0 flex-1 basis-64 gap-2.5 text-label text-foreground">
        <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", iconColor)} />
        <span className="min-w-0 [overflow-wrap:anywhere]">
          <strong className="font-bold">Why this trace:</strong>{" "}
          <Sentence item={item} text={reasonSentence(item, trace)} />
        </span>
      </p>
      {also.length > 0 ? (
        <p className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-muted-foreground">
          Also:
          {also.map((other) => (
            <ReasonChip key={other} reason={other} />
          ))}
        </p>
      ) : null}
    </div>
  );
}

/** The sentence with its backticked names set as code; a finding's name links to the insight. */
function Sentence({ item, text }: { item: QueueItem; text: string }) {
  const { orgId, projectId } = useProjectParams();
  const insightId = item.reason_detail?.insight_id;
  const colon = text.indexOf(": ");
  if (item.reason === "doctor_finding" && insightId !== undefined && colon !== -1) {
    return (
      <>
        {text.slice(0, colon + 2)}
        <Link
          to="/$orgId/$projectId/doctor/$insightId"
          params={{ orgId, projectId, insightId }}
          className="font-semibold text-accent underline underline-offset-2"
        >
          {text.slice(colon + 2)}
        </Link>
      </>
    );
  }
  return <>{withCode(text)}</>;
}

/** Splits on backticks: every odd part was a name and renders as `<code>`. */
function withCode(text: string): ReactNode[] {
  return text.split("`").map((part, index) =>
    index % 2 === 1 ? (
      <code key={index} className="font-mono text-[0.95em]">
        {part}
      </code>
    ) : (
      part
    ),
  );
}
