import { CircleAlert } from "lucide-react";

import { CopyButton } from "@/components/copy-button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { Span } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

import { PromptVersionRef } from "./prompt-version-ref";
import { SpanKindBadge } from "./span-kind";
import { SpanStatusBadge } from "./status";
import { TruncatedBadge } from "./truncated-badge";

/** The inspector's span header: name and ID, kind, status, duration, model and the error. */
export function SpanDetailHeader({ span }: { span: Span }) {
  return (
    <header className="flex min-w-0 flex-col gap-2">
      <div className="flex min-w-0 items-center gap-2">
        <h2 title={span.name} className="min-w-0 flex-1 truncate text-card text-foreground">
          {span.name}
        </h2>
        <CopyButton value={span.span_id} label="Copy span ID" className="size-7 shrink-0" />
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <SpanKindBadge kind={span.kind} />
        <SpanStatusBadge status={span.status} />
        {span.truncated ? <TruncatedBadge /> : null}
        <span className="tabular">{formatDuration(span.duration_ms)}</span>
        {span.model ? (
          <>
            <span aria-hidden className="text-subtle-foreground">
              ·
            </span>
            <span title={span.model} className="max-w-56 truncate font-mono text-label font-normal">
              {span.model}
            </span>
          </>
        ) : null}
        {span.prompt_version_id ? <PromptVersionRef versionId={span.prompt_version_id} /> : null}
      </div>
      {span.status === "error" ? (
        <div
          role="note"
          className="mt-1 flex items-start gap-2 rounded-input bg-danger-subtle px-3 py-2.5 text-danger-text"
        >
          <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
          <p className="min-w-0 font-mono text-xs leading-5 break-words whitespace-pre-wrap">
            {span.status_message ?? "The span failed without an error message."}
          </p>
        </div>
      ) : null}
    </header>
  );
}

export function SpanDetailSkeleton({ className }: { className?: string }) {
  return (
    <Card aria-hidden className={cn("flex flex-col gap-4 p-5 sm:p-6", className)}>
      <Skeleton className="h-5 w-48" />
      <div className="flex gap-1.5">
        <Skeleton className="h-6 w-11 rounded-full" />
        <Skeleton className="h-6 w-10 rounded-full" />
        <Skeleton className="h-6 w-28 rounded-full" />
      </div>
      <Skeleton className="h-[42px] w-full rounded-full" />
      <Skeleton className="h-20 w-[88%] self-center rounded-tile" />
      <Skeleton className="h-28 w-[85%] self-end rounded-tile" />
      <Skeleton className="h-36 w-[85%] rounded-tile" />
    </Card>
  );
}
