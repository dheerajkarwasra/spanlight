import { Info } from "lucide-react";
import { useId, useState } from "react";

import { ColumnLabels, TileList, TileListSkeleton } from "@/components/tile-list";
import { ErrorState } from "@/components/error-state";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import type { ErrorClass, RateLimitKind } from "@/lib/api";
import { ERROR_CLASSES, errorClassLabel } from "@/lib/error-class";
import { RATE_LIMIT_KIND_ORDER, rateLimitKindLabel } from "@/lib/rate-limit-kind";
import { cn } from "@/lib/utils";

import { FilterPill } from "./filter-pill";
import { windowLength } from "./providers-format";
import { useErrorSamples } from "./providers-queries";
import { SAMPLE_COLUMNS } from "./sample-columns";
import { SampleRow } from "./sample-row";

/** Rows shown before "Show all". */
const FIRST_ROWS = 6;

const CLASS_OPTIONS = ERROR_CLASSES.map((value) => ({ value, label: errorClassLabel(value) }));
const KIND_OPTIONS = RATE_LIMIT_KIND_ORDER.map((value) => ({
  value,
  label: rateLimitKindLabel(value),
}));

/**
 * Figma "Sample failures card": the newest failed LLM calls, each opening its trace. The server
 * reads samples from at most the window's last 24 hours and says so (`clamped`).
 */
export function SampleTraces({ windowMs }: { windowMs: number }) {
  const titleId = useId();
  const [errorClass, setErrorClass] = useState<ErrorClass | undefined>();
  const [kind, setKind] = useState<RateLimitKind | undefined>();
  const [showAll, setShowAll] = useState(false);
  const samples = useErrorSamples({ errorClass, kind });
  const items = samples.data?.items ?? [];
  const visible = showAll ? items : items.slice(0, FIRST_ROWS);

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-4 p-5 sm:p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <CardTitle id={titleId}>Sample failures</CardTitle>
          <p className="text-xs font-medium text-muted-foreground">
            The {items.length > 0 ? items.length : 20} newest failed LLM calls in the window, newest
            first. Each row opens its trace.
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-3">
          <FilterPill
            label="Class"
            value={errorClass}
            options={CLASS_OPTIONS}
            onChange={setErrorClass}
          />
          <FilterPill label="Kind" value={kind} options={KIND_OPTIONS} onChange={setKind} />
        </div>
      </div>
      {samples.data?.clamped ? (
        <Notice tone="warning" icon={Info}>
          Window is {windowLength(windowMs)}; samples from the last 24 hours. Counts and charts
          above cover the whole window.
        </Notice>
      ) : null}
      {samples.isPending ? (
        <TileListSkeleton label="Loading sample failures" rows={4} className="h-[41px]" />
      ) : samples.isError ? (
        <ErrorState
          compact
          error={samples.error}
          onRetry={() => void samples.refetch()}
          title="Couldn't load sample failures"
        />
      ) : items.length === 0 ? (
        <p className="rounded-tile bg-surface-muted px-4 py-6 text-center text-sm text-muted-foreground">
          {errorClass || kind
            ? "No failed calls match these filters"
            : "No failed LLM calls in this window"}
        </p>
      ) : (
        <div
          className={cn(
            "@container flex flex-col gap-1.5",
            samples.isPlaceholderData && "opacity-60",
          )}
        >
          <ColumnLabels className="px-3 @[46rem]:flex">
            <span className={SAMPLE_COLUMNS.time}>Time</span>
            <span className={SAMPLE_COLUMNS.model}>Model</span>
            <span className={SAMPLE_COLUMNS.errorClass}>Class</span>
            <span className={SAMPLE_COLUMNS.kind}>Kind</span>
            <span className={SAMPLE_COLUMNS.typeCode}>Type / code</span>
            <span className={SAMPLE_COLUMNS.retryAfter}>Retry-after</span>
            <span className={SAMPLE_COLUMNS.attempts}>Attempts</span>
            <span aria-hidden className="w-4 shrink-0" />
          </ColumnLabels>
          <TileList label="Sample failures">
            {visible.map((sample) => (
              <SampleRow key={sample.span_id} sample={sample} />
            ))}
          </TileList>
          <div className="flex flex-wrap items-center gap-3 pt-2.5">
            <p className="min-w-0 flex-1 text-xs font-medium text-subtle-foreground">
              Showing {visible.length} of {items.length} · — means the provider sent no value, or
              the call was not a capacity refusal
            </p>
            {visible.length < items.length ? (
              <Button
                onClick={() => {
                  setShowAll(true);
                }}
              >
                Show all {items.length}
              </Button>
            ) : null}
          </div>
        </div>
      )}
    </Card>
  );
}
