import { ChevronDown, ChevronUp } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { ErrorState } from "@/components/error-state";
import { UnknownValue } from "@/components/unknown-value";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { CostExplanation, ExplanationStatus, Span } from "@/lib/api";

import { formatExactUsd, statusCopy } from "./cost-explanation";
import { useCostExplanationQuery } from "./cost-explanation-queries";
import { SpanCostBody } from "./span-cost-body";
import { StatusChip } from "./span-cost-status";

interface SpanCostCardProps {
  span: Span;
  traceId: string;
}

/**
 * Figma "Span cost card / *": the lines behind a span's stored cost. Collapsed by default; the
 * explanation is fetched the first time the card is expanded and kept for the session.
 */
export function SpanCostCard({ span, traceId }: SpanCostCardProps) {
  const [expanded, setExpanded] = useState(false);
  const panelId = useId();
  const query = useCostExplanationQuery(traceId, span.span_id, { enabled: expanded });
  const loading = expanded && query.isPending;
  const busy = expanded && query.isFetching;

  return (
    <Card className="flex flex-col gap-3.5 p-5">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <h2 className="shrink-0 text-card text-foreground">Cost breakdown</h2>
        <HeaderFigure span={span} data={query.data} failed={query.isError} loading={loading} />
        <Button
          variant="secondary"
          size="icon-sm"
          className="ml-auto border-border-strong shadow-none max-sm:size-11"
          aria-expanded={expanded}
          aria-controls={panelId}
          aria-label={expanded ? "Hide cost breakdown" : "Show cost breakdown"}
          onClick={() => {
            setExpanded((open) => !open);
          }}
        >
          {expanded ? <ChevronUp aria-hidden /> : <ChevronDown aria-hidden />}
        </Button>
      </div>
      {expanded ? (
        <div
          id={panelId}
          data-testid="span-cost-body"
          aria-busy={busy}
          className="flex flex-col gap-3.5"
        >
          {query.isError ? (
            <div className="rounded-tile bg-surface-muted">
              <ErrorState
                compact
                error={query.error}
                title="Couldn't load the cost breakdown"
                description="The request failed. The span's stored cost above is unchanged; try again."
                retrying={query.isFetching}
                onRetry={() => {
                  void query.refetch();
                }}
              />
            </div>
          ) : query.data === undefined ? (
            <CostSkeleton />
          ) : (
            <SpanCostBody explanation={query.data} span={span} />
          )}
        </div>
      ) : null}
    </Card>
  );
}

function unknownCostReason(status: ExplanationStatus | null): string {
  if (status === "no_price") {
    return "No price row matched, so the cost is unknown";
  }
  if (status === "no_usage") {
    return "Usage was not reported, so the cost is unknown";
  }
  return "No cost was stored for this span";
}

interface HeaderFigureProps {
  span: Span;
  data: CostExplanation | undefined;
  failed: boolean;
  loading: boolean;
}

/** "· $0.063570 · Matches" after the title: the stored figure and, once loaded, its status. */
function HeaderFigure({ span, data, failed, loading }: HeaderFigureProps) {
  if (loading) {
    return <Skeleton className="h-3 w-22 rounded-full" />;
  }
  if (failed && data === undefined) {
    return (
      <Separated>
        <UnknownValue reason="Cost breakdown not loaded" className="font-mono text-label" />
      </Separated>
    );
  }
  const stored = formatExactUsd(data ? data.stored_usd : span.cost_usd);
  const copy = data ? statusCopy(data.status, data.message) : null;
  return (
    <span className="flex items-center gap-2 max-sm:order-last max-sm:basis-full">
      <Separated>
        {stored === null ? (
          <UnknownValue reason={unknownCostReason(data?.status ?? null)} className="font-mono" />
        ) : (
          <span className="font-mono text-label text-foreground tabular">{stored}</span>
        )}
      </Separated>
      {copy !== null ? (
        <Separated>
          <StatusChip tone={copy.tone} title={copy.title} />
        </Separated>
      ) : null}
    </span>
  );
}

function Separated({ children }: { children: ReactNode }) {
  return (
    <>
      <span aria-hidden className="text-sm font-medium text-subtle-foreground max-sm:hidden">
        ·
      </span>
      {children}
    </>
  );
}

/** Figma "Span cost card / loading": four table-height rows and a polite status line. */
function CostSkeleton() {
  return (
    <>
      <div aria-hidden className="flex flex-col gap-1.5">
        {[120, 150, 120, 150].map((width, index) => (
          <div key={index} className="flex items-center gap-3 rounded-input bg-surface-muted p-3">
            <div className="h-2.5 rounded-full bg-border" style={{ width }} />
            <div className="ml-auto h-2.5 w-18 rounded-full bg-border" />
          </div>
        ))}
      </div>
      <p role="status" aria-live="polite" className="text-xs font-medium text-subtle-foreground">
        Loading the cost explanation…
      </p>
    </>
  );
}
