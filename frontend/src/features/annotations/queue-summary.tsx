import { Check, List, SkipForward } from "lucide-react";

import { Card } from "@/components/ui/card";
import type { AnnotationQueue, ScoreConfig } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { ProgressBar } from "./progress-bar";
import { configSummary, progressTotal, queueConfigs } from "./queue-format";

function count(value: number): string {
  return formatInteger(value) ?? String(value);
}

interface QueueSummaryProps {
  queue: AnnotationQueue;
  configs: readonly ScoreConfig[] | undefined;
}

/** Figma "Review — Queue detail": the ink progress card and the instructions card. */
export function QueueSummary({ queue, configs }: QueueSummaryProps) {
  const { pending, done, skipped } = queue.progress;
  const chosen = queueConfigs(queue, configs);
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <Card variant="hero" className="flex flex-col gap-3 p-6">
        <h2 className="text-overline text-rail-muted-foreground uppercase">Progress</h2>
        <p className="flex items-baseline gap-2">
          <span className="text-[44px] leading-none font-extrabold tracking-[-0.04em] tabular">
            {count(done)}
          </span>
          <span className="text-sm font-medium text-rail-muted-foreground">
            of {count(progressTotal(queue.progress))} done
          </span>
        </p>
        <ProgressBar progress={queue.progress} tone="ink" className="max-w-[420px]" />
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs font-semibold">
          <li className="flex items-center gap-1.5">
            <List aria-hidden className="size-3.5" />
            {count(pending)} pending
          </li>
          <li className="flex items-center gap-1.5">
            <Check aria-hidden className="size-3.5" />
            {count(done)} done
          </li>
          <li className="flex items-center gap-1.5">
            <SkipForward aria-hidden className="size-3.5" />
            {count(skipped)} skipped
          </li>
        </ul>
      </Card>
      <Card className="flex flex-col gap-2.5 p-5">
        <h2 className="text-overline text-muted-foreground uppercase">Instructions</h2>
        <p className="text-label whitespace-pre-wrap text-foreground">
          {queue.instructions ?? (
            <span className="text-muted-foreground">No instructions for this queue.</span>
          )}
        </p>
        <ul className="flex flex-wrap gap-1.5" aria-label="Scores collected">
          {chosen.map((config) => (
            <li
              key={config.id}
              className="rounded-full border border-border-strong bg-surface px-2.5 py-0.5 text-2xs font-medium text-foreground"
            >
              {configSummary(config)}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
