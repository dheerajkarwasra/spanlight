import { Link } from "@tanstack/react-router";
import { Info } from "lucide-react";
import { useId } from "react";

import { NO_PRICE_REASON } from "@/components/cost-value";
import { ErrorState } from "@/components/error-state";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell/project-context";
import { SERIES_COLORS } from "@/lib/chart-theme";
import { formatCost, formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

import { shortRangeLabel } from "./hero";
import { SetPricesLink } from "./model-cells";
import { spendByModel, type ModelSpend } from "./models";
import { formatShare } from "./spend";
import type { OverviewQueries } from "./use-overview-queries";

const CARD_CLASSES = "flex min-w-0 flex-col gap-4 p-5 sm:p-6";

/**
 * "What changed" spend (spec §4, ruling 14): the five most expensive models as horizontal bars
 * with their cost and share of the priced spend, each row opening that model's traces.
 */
export function SpendByModel({
  queries,
  className,
}: {
  queries: OverviewQueries;
  className?: string;
}) {
  const titleId = useId();
  const { models, overview, range } = queries;
  const unpriced = overview.data?.current.unpriced_calls ?? 0;
  // With no priced call the spend is unknown, not a lower bound.
  const nonePriced = overview.data?.current.cost_usd == null;

  return (
    <Card role="region" aria-labelledby={titleId} className={cn(CARD_CLASSES, className)}>
      <div className="flex flex-col gap-0.5">
        <CardTitle id={titleId}>Spend by model</CardTitle>
        <CardDescription className="mt-0 font-medium">
          Top 5 by cost · {shortRangeLabel(range)}
        </CardDescription>
      </div>
      {models.isPending ? (
        <div aria-hidden className="flex flex-col gap-3">
          {Array.from({ length: 5 }, (_, index) => (
            <Skeleton key={index} className="h-9 w-full" />
          ))}
        </div>
      ) : models.isError ? (
        <ErrorState
          compact
          error={models.error}
          title="Couldn't load spend by model"
          onRetry={() => {
            void models.refetch();
          }}
        />
      ) : (
        <SpendRows rows={spendByModel(models.data)} dim={models.isPlaceholderData} />
      )}
      {unpriced > 0 ? (
        <p className="mt-auto flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <Info aria-hidden className="size-[13px] shrink-0" />
          <span>
            {nonePriced
              ? "No priced calls in this window"
              : `${formatInteger(unpriced) ?? ""} ${unpriced === 1 ? "call" : "calls"} unpriced · spend is a lower bound`}
          </span>
          <span aria-hidden>·</span>
          <SetPricesLink />
        </p>
      ) : null}
    </Card>
  );
}

function SpendRows({ rows, dim }: { rows: ModelSpend[]; dim: boolean }) {
  const { orgId, projectId } = useProjectParams();
  if (rows.length === 0) {
    return <p className="py-4 text-sm text-muted-foreground">No LLM calls in this period</p>;
  }
  return (
    <ul className={cn("flex flex-col gap-1 transition-opacity", dim && "opacity-60")}>
      {rows.map((row) => {
        const name = row.model ?? "Unknown model";
        const body = <SpendRow row={row} name={name} />;
        return (
          <li key={`${row.provider ?? "∅"}/${name}`}>
            {row.model === null ? (
              <div className="px-2 py-1.5">{body}</div>
            ) : (
              <Link
                to="/$orgId/$projectId/traces"
                params={{ orgId, projectId }}
                search={{ model: row.model }}
                title={`View traces using ${row.model}`}
                className="block rounded-tile px-2 py-1.5 hover:bg-surface-hover"
              >
                {body}
              </Link>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Name, cost and share on one line; the bar under it, its length the share (4 px round end). */
function SpendRow({ row, name }: { row: ModelSpend; name: string }) {
  return (
    <span className="flex flex-col gap-1.5">
      <span className="flex items-baseline gap-3 text-sm">
        <span className="min-w-0 flex-1 truncate font-mono text-label text-foreground">{name}</span>
        {row.costUsd === null ? (
          // A focusable tooltip can't sit in the row's link; the reason is read instead.
          <span title={NO_PRICE_REASON} className="shrink-0 cursor-help text-subtle-foreground">
            <span aria-hidden>—</span>
            <span className="sr-only">Unknown: {NO_PRICE_REASON}</span>
          </span>
        ) : (
          <span className="shrink-0 font-semibold text-foreground tabular">
            {formatCost(row.costUsd)}
            <span className="ml-2 font-medium text-muted-foreground">
              {row.share === null ? null : formatShare(row.share)}
              <span className="sr-only"> of spend</span>
            </span>
          </span>
        )}
      </span>
      <span aria-hidden className="block h-2 w-full">
        {row.share ? (
          <span
            className="block h-full rounded-r-[4px]"
            style={{
              width: `${String(Math.max(row.share * 100, 1))}%`,
              backgroundColor: SERIES_COLORS.cost,
            }}
          />
        ) : null}
      </span>
    </span>
  );
}
