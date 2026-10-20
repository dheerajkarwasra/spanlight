import { useMemo, useState, type CSSProperties } from "react";

import { ErrorState } from "@/components/error-state";
import { ListFooter } from "@/components/list-footer";
import { SectionCard } from "@/components/section-card";
import { ColumnLabels, TileListSkeleton } from "@/components/tile-list";
import { SegmentedControl } from "@/components/ui/segmented-control";
import type { ExperimentItem, ExperimentRun } from "@/lib/api";
import { pluralize } from "@/lib/format";

import type { useRunItemsQuery } from "./experiment-queries";
import { ItemRow } from "./run-item-row";
import { leadScore, type ScoreMeta } from "./score-meta";

type Filter = "all" | "errors" | "lowest";

interface RunItemsProps {
  /** The run's items, read page by page by the page. */
  query: ReturnType<typeof useRunItemsQuery>;
  run: ExperimentRun;
  scores: readonly ScoreMeta[];
}

function scoreNumber(item: ExperimentItem, name: string): number {
  const value = item.scores[name];
  if (typeof value === "number") return value;
  if (typeof value === "boolean") return value ? 1 : 0;
  return Number.POSITIVE_INFINITY;
}

/** Figma "Experiments — Run" Items card: every item's output, scores, latency and cost. */
export function RunItems({ query, run, scores }: RunItemsProps) {
  const [filter, setFilter] = useState<Filter>("all");
  const lead = leadScore(scores);
  const all = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const shown = useMemo(() => {
    if (filter === "errors") return all.filter((item) => item.error !== null);
    if (filter === "lowest" && lead) {
      return [...all].sort((x, y) => scoreNumber(x, lead.name) - scoreNumber(y, lead.name));
    }
    return all;
  }, [all, filter, lead]);
  const columns = {
    "--cols": `minmax(0,1.3fr) minmax(0,1.6fr) ${scores.map(() => "minmax(0,0.6fr)").join(" ")} minmax(0,0.6fr) minmax(0,0.6fr)`,
  } as CSSProperties;
  const options = [
    { value: "all", label: "All" },
    { value: "errors", label: "With errors" },
    ...(lead && lead.kind !== "categorical"
      ? [{ value: "lowest", label: `Lowest ${lead.name}` }]
      : []),
  ] as { value: Filter; label: string }[];

  return (
    <SectionCard
      title="Items"
      description={`${pluralize(run.totals?.items ?? all.length, "item")} in dataset order`}
      className="gap-3"
    >
      <SegmentedControl<Filter>
        aria-label="Show items"
        value={filter}
        onValueChange={setFilter}
        options={options}
        className="self-start"
      />
      {query.isPending ? (
        <TileListSkeleton label="Loading items" rows={4} className="h-[72px]" />
      ) : query.isError && all.length === 0 ? (
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load items"
          onRetry={() => void query.refetch()}
          className="rounded-tile bg-surface-muted"
        />
      ) : shown.length === 0 ? (
        <p className="rounded-tile bg-surface-muted px-4 py-8 text-center text-sm text-muted-foreground">
          {filter === "errors" ? "No item of this run failed." : "No items have run yet."}
        </p>
      ) : (
        <div className="flex flex-col gap-1.5" style={columns}>
          <ColumnLabels className="px-4 lg:grid lg:grid-cols-[var(--cols)] lg:gap-3">
            <span>Item</span>
            <span>Output</span>
            {scores.map((meta) => (
              <span key={meta.name} className="truncate">
                {meta.name}
              </span>
            ))}
            <span>Latency</span>
            <span>Cost</span>
          </ColumnLabels>
          <ul aria-label="Items" className="flex flex-col gap-1.5">
            {shown.map((item) => (
              <ItemRow key={item.dataset_item_id} item={item} scores={scores} />
            ))}
          </ul>
          {query.hasNextPage ? (
            <ListFooter
              layout="card"
              summary={`Showing ${pluralize(all.length, "item")}`}
              hasNextPage
              isFetchingNextPage={query.isFetchingNextPage}
              onLoadMore={() => void query.fetchNextPage()}
            />
          ) : null}
          <p className="text-xs text-muted-foreground">
            A blank score means the evaluator did not score the item, usually because its monthly
            budget was spent. It never fails the item.
          </p>
        </div>
      )}
    </SectionCard>
  );
}
