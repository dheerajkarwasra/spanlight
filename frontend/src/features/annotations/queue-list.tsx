import { Link } from "@tanstack/react-router";
import { Check, ChevronRight } from "lucide-react";

import { RelativeTime } from "@/components/relative-time";
import { useProjectParams } from "@/features/shell";
import type { AnnotationQueue, ScoreConfig } from "@/lib/api";
import { cn } from "@/lib/utils";

import { ProgressBar } from "./progress-bar";
import { progressSummary, progressTotal, queueConfigs } from "./queue-format";

const COLUMNS =
  "md:grid md:grid-cols-[minmax(0,1fr)_minmax(0,180px)_minmax(0,170px)_96px_16px] md:items-center md:gap-4";

interface QueueListProps {
  queues: readonly AnnotationQueue[];
  configs: readonly ScoreConfig[] | undefined;
}

/** Figma "Review — Queues list": tile rows on wide screens, stacked tiles at 375 px. */
export function QueueList({ queues, configs }: QueueListProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <div
        aria-hidden
        className={cn(
          COLUMNS,
          "hidden px-5 py-2 text-overline font-semibold text-subtle-foreground uppercase",
        )}
      >
        <span>Queue</span>
        <span>Progress</span>
        <span>Scores</span>
        <span>Created</span>
      </div>
      <ul className="flex flex-col gap-1.5" aria-label="Annotation queues">
        {queues.map((queue) => (
          <li key={queue.id}>
            <QueueRow queue={queue} configs={configs} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function QueueRow({
  queue,
  configs,
}: {
  queue: AnnotationQueue;
  configs: QueueListProps["configs"];
}) {
  const { orgId, projectId } = useProjectParams();
  const total = progressTotal(queue.progress);
  const finished = total > 0 && queue.progress.pending === 0;
  const names = queueConfigs(queue, configs).map((config) => config.name);
  return (
    <Link
      to="/$orgId/$projectId/queues/$queueId"
      params={{ orgId, projectId, queueId: queue.id }}
      className={cn(
        COLUMNS,
        "flex flex-col gap-2.5 rounded-tile bg-surface-muted px-4 py-3.5 transition-colors hover:bg-surface-hover md:px-5",
      )}
    >
      <span className="flex min-w-0 flex-col gap-[3px]">
        <span className="flex items-center justify-between gap-2">
          <span className="font-mono text-[13px] font-semibold [overflow-wrap:anywhere] text-foreground">
            {queue.name}
          </span>
          <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground md:hidden" />
        </span>
        {queue.instructions ? (
          <span className="line-clamp-2 text-xs text-muted-foreground md:line-clamp-1">
            {queue.instructions}
          </span>
        ) : null}
      </span>
      <span className="flex flex-col gap-1.5">
        <ProgressBar progress={queue.progress} />
        <span className="flex items-center gap-1 text-xs font-semibold text-foreground">
          {finished ? (
            <Check aria-hidden className="size-3.5 text-success" strokeWidth={2.5} />
          ) : null}
          {progressSummary(queue.progress)}
        </span>
      </span>
      <span className="flex flex-wrap gap-1.5">
        <span className="sr-only">Scores:</span>
        {names.map((name) => (
          <span
            key={name}
            className="rounded-full border border-border-strong bg-surface px-2.5 py-0.5 text-2xs font-medium text-foreground"
          >
            {name}
          </span>
        ))}
      </span>
      <span className="text-xs font-medium text-muted-foreground">
        <span className="md:sr-only">Created </span>
        <RelativeTime iso={queue.created_at} />
      </span>
      <ChevronRight aria-hidden className="hidden size-4 text-muted-foreground md:block" />
    </Link>
  );
}
