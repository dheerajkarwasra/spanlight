import { Boxes, ChevronDown } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { ModelMetrics } from "@/lib/api";
import { cn } from "@/lib/utils";

import { SetPricesLink } from "./model-cells";
import { ModelTiles } from "./model-tiles";
import { ModelsTable } from "./model-wide-table";
import { MODEL_PREVIEW_LIMIT, sortModelsByCalls, unpricedCalls } from "./models";

interface ModelTableProps {
  models: ModelMetrics[];
  isRefreshing: boolean;
  className?: string;
}

/**
 * Per-model usage, latency and cost (Figma "Models card"). A real table when the card is wide;
 * on narrow cards each model becomes a tile with the three numbers that matter most.
 */
export function ModelTable({ models, isRefreshing, className }: ModelTableProps) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  const sorted = sortModelsByCalls(models);
  const canExpand = sorted.length > MODEL_PREVIEW_LIMIT;
  const visible = expanded ? sorted : sorted.slice(0, MODEL_PREVIEW_LIMIT);

  return (
    <ModelTableCard
      className={className}
      description={
        <>
          <span className="@4xl:hidden">
            Sorted by calls
            {canExpand && !expanded ? ` · top ${visible.length} of ${sorted.length}` : ""}
          </span>
          <span className="hidden @4xl:inline">
            Latency, tokens and cost per model · sorted by calls
          </span>
        </>
      }
      action={
        canExpand ? (
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={listId}
            onClick={() => {
              setExpanded((current) => !current);
            }}
            className="inline-flex shrink-0 items-center gap-1 rounded-sm text-sm font-semibold text-accent hover:underline hover:underline-offset-4"
          >
            {expanded ? "Top models" : "All models"}
            <ChevronDown
              aria-hidden
              className={cn("size-3.5 transition-transform", expanded && "rotate-180")}
            />
          </button>
        ) : null
      }
    >
      {sorted.length === 0 ? (
        <EmptyState
          icon={Boxes}
          title="No model calls in this period"
          description="Per-model latency, tokens and cost appear here once your app makes LLM calls."
          className="py-8"
        />
      ) : (
        <div id={listId} className={cn("transition-opacity", isRefreshing && "opacity-60")}>
          <ModelsTable models={visible} />
          <ModelTiles models={visible} />
          <p className="hidden border-t border-border px-5 pt-3.5 pb-3 text-xs font-medium text-muted-foreground @4xl:block">
            {visible.length < sorted.length
              ? `Showing top ${visible.length} of ${sorted.length} models`
              : `Showing all ${sorted.length} ${sorted.length === 1 ? "model" : "models"}`}
            {unpricedCalls(visible) > 0 ? (
              <>
                {" · — means no price. "}
                <SetPricesLink />
              </>
            ) : null}
          </p>
        </div>
      )}
    </ModelTableCard>
  );
}

interface ModelTableCardProps {
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}

function ModelTableCard({ description, action, className, children }: ModelTableCardProps) {
  return (
    <Card className={cn("@container flex min-w-0 flex-col p-3 sm:p-2", className)}>
      <div className="flex items-center justify-between gap-3 px-2 py-1.5 sm:px-5 sm:py-3.5">
        <div className="flex min-w-0 flex-col gap-0.5">
          <CardTitle>Models</CardTitle>
          {description ? (
            <CardDescription className="mt-0 font-medium">{description}</CardDescription>
          ) : null}
        </div>
        {action}
      </div>
      {children}
    </Card>
  );
}

export function ModelTableSkeleton({ className }: { className?: string }) {
  return (
    <ModelTableCard className={className}>
      <div aria-hidden className="mt-2 flex flex-col gap-2 @4xl:mt-0 @4xl:gap-0">
        <Skeleton className="hidden h-[34px] rounded-tile @4xl:block" />
        {Array.from({ length: 4 }, (_, index) => (
          <div
            key={index}
            className="flex h-[102px] items-center gap-4 rounded-tile bg-surface-muted px-5 @4xl:h-14 @4xl:rounded-none @4xl:border-b @4xl:border-border @4xl:bg-transparent"
          >
            <Skeleton className="h-4 w-40" />
            <Skeleton className="ml-auto h-4 w-14" />
            <Skeleton className="hidden h-4 w-14 @4xl:block" />
            <Skeleton className="hidden h-4 w-16 @4xl:block" />
            <Skeleton className="h-4 w-16" />
          </div>
        ))}
      </div>
    </ModelTableCard>
  );
}

interface ModelTableErrorProps {
  error: unknown;
  onRetry: () => void;
  className?: string;
}

export function ModelTableError({ error, onRetry, className }: ModelTableErrorProps) {
  return (
    <ModelTableCard className={className}>
      <ErrorState compact error={error} onRetry={onRetry} title="Couldn't load model metrics" />
    </ModelTableCard>
  );
}
