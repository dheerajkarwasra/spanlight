import { Link } from "@tanstack/react-router";
import { ChevronRight, CircleCheck, Info } from "lucide-react";
import { useId, type ReactNode } from "react";

import { ErrorState } from "@/components/error-state";
import { UnknownValue } from "@/components/unknown-value";
import { Badge } from "@/components/ui/badge";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { TooltipContent, TooltipRoot, TooltipTrigger } from "@/components/ui/tooltip";
import { useProjectParams } from "@/features/shell/project-context";
import type { Completeness } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { useTapTooltip } from "@/lib/use-tap-tooltip";
import { cn } from "@/lib/utils";

import {
  COMPLETENESS_DEFINITION,
  LOWER_BOUND_REASON,
  NO_RATIO_REASON,
  SDK_UNREPORTED_REASON,
  completenessReason,
  completenessText,
  lossTiles,
  storedLine,
  totalLost,
} from "./completeness-format";

const CARD_CLASSES = "@container flex min-w-0 flex-col gap-4 p-5 sm:p-6";

interface CompletenessCardProps {
  result: Completeness;
  /** Lower-case window label for the description, e.g. "last 24h". */
  rangeLabel: string;
  className?: string;
}

/**
 * Figma "Overview/Completeness card": the share of spans that reached storage, never rounded up
 * (it reads 100 % only when nothing was lost), the losses per source, a "lower bound" note when
 * SDK failures and ingest rejections may count one span twice, and a plain link to the Provider
 * health page (which has no per-reason losses breakdown, so the link promises none). A source that
 * reported nothing reads "none reported" with a dash, since its silence is not a zero.
 */
export function CompletenessCard({ result, rangeLabel, className }: CompletenessCardProps) {
  const titleId = useId();
  const ratio = completenessText(result);
  if (totalLost(result.losses) === 0) {
    // Nothing lost: one line (spec O8); the zero is a lower bound, as most senders report nothing.
    return (
      <Card
        className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3 sm:px-6", className)}
      >
        <CircleCheck aria-hidden className="size-4 shrink-0 text-success" />
        <p className="text-sm font-semibold text-foreground">No spans lost in this window.</p>
        <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          ≈ lower bound
          <InfoButton label="Why this is a lower bound" content={SDK_UNREPORTED_REASON} />
        </span>
      </Card>
    );
  }
  return (
    <Card role="region" aria-labelledby={titleId} className={cn(CARD_CLASSES, className)}>
      <div className="flex flex-col gap-4 @4xl:flex-row @4xl:items-center @4xl:gap-6">
        <CompletenessHeader
          titleId={titleId}
          rangeLabel={rangeLabel}
          reason={completenessReason(result)}
        />
        <div className="flex flex-col gap-4 @4xl:flex-1 @4xl:flex-row @4xl:items-center @4xl:gap-6">
          <div className="flex shrink-0 flex-col items-start gap-1.5 @4xl:w-[200px]">
            <p className="text-metric text-foreground tabular">
              {ratio ?? <UnknownValue reason={NO_RATIO_REASON} />}
            </p>
            {result.lower_bound ? (
              <div className="flex items-center gap-1.5">
                <Badge>lower bound</Badge>
                <InfoButton label="Why this is a lower bound" content={LOWER_BOUND_REASON} />
              </div>
            ) : null}
            <p className="text-xs font-medium text-muted-foreground">{storedLine(result)}</p>
          </div>
          <ul
            aria-label="Spans lost by source"
            className="grid min-w-0 flex-1 grid-cols-3 divide-x divide-border"
          >
            {lossTiles(result.losses).map((tile) => (
              <li
                key={tile.source}
                className="flex min-w-0 flex-col gap-1 px-4 first:pl-0 last:pr-0 max-sm:px-3"
              >
                <span className="text-xs font-medium break-words text-muted-foreground">
                  {tile.label}
                </span>
                <span className="text-h2 text-foreground tabular">
                  {tile.count === null ? (
                    <UnknownValue reason={tile.unknownReason ?? SDK_UNREPORTED_REASON} />
                  ) : (
                    formatInteger(tile.count)
                  )}
                </span>
                <span className="text-xs font-medium break-words text-muted-foreground">
                  {tile.detail}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Card>
  );
}

interface CompletenessHeaderProps {
  titleId?: string;
  rangeLabel: string;
  /** The info tooltip; the plain definition while the result is unknown. */
  reason?: string;
}

function CompletenessHeader({ titleId, rangeLabel, reason }: CompletenessHeaderProps) {
  const { orgId, projectId } = useProjectParams();
  return (
    <div className="flex flex-col gap-1 @lg:flex-row @lg:items-start @lg:gap-3 @4xl:w-[300px] @4xl:shrink-0 @4xl:flex-col @4xl:gap-1">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex items-center gap-1.5">
          <CardTitle id={titleId}>Telemetry completeness</CardTitle>
          <InfoButton
            label="How completeness is measured"
            content={reason ?? COMPLETENESS_DEFINITION}
          />
        </div>
        <p className="text-xs font-medium text-muted-foreground">
          {COMPLETENESS_DEFINITION} · {rangeLabel}
        </p>
      </div>
      <Link
        to="/$orgId/$projectId/providers"
        params={{ orgId, projectId }}
        className="inline-flex min-h-11 w-fit shrink-0 items-center gap-1 rounded-sm text-sm font-medium text-accent hover:underline"
      >
        Provider health
        <ChevronRight aria-hidden className="size-4" />
      </Link>
    </div>
  );
}

/** An info icon whose tooltip opens on hover, focus and (on phones) tap; 44 px hit area. */
function InfoButton({ label, content }: { label: string; content: ReactNode }) {
  const { rootProps, triggerProps } = useTapTooltip();
  return (
    <TooltipRoot {...rootProps}>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          {...triggerProps}
          className="relative inline-flex size-4 items-center justify-center rounded-full text-muted-foreground after:absolute after:-inset-3.5"
        >
          <Info aria-hidden className="size-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent>{content}</TooltipContent>
    </TooltipRoot>
  );
}

export function CompletenessCardSkeleton({ className }: { className?: string }) {
  return (
    <Card aria-hidden className={cn(CARD_CLASSES, className)}>
      <Skeleton className="h-4 w-48 rounded-full" />
      <Skeleton className="h-3 w-80 max-w-full rounded-full" />
      <Skeleton className="h-8 w-28 rounded-tile" />
      <Skeleton className="h-20 w-full rounded-tile" />
    </Card>
  );
}

interface CompletenessCardErrorProps {
  error: unknown;
  onRetry: () => void;
  className?: string;
}

/** The request failed: this card says so and offers a retry; the rest of the page renders. */
export function CompletenessCardError({ error, onRetry, className }: CompletenessCardErrorProps) {
  return (
    <Card className={cn(CARD_CLASSES, className)}>
      <ErrorState
        compact
        error={error}
        onRetry={onRetry}
        title="Telemetry completeness didn't load"
      />
    </Card>
  );
}
