import { useId, useState } from "react";

import { Card, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { SegmentedControl } from "@/components/ui/segmented-control";
import type { ItemDelta } from "@/lib/api";
import { formatInteger, pluralize } from "@/lib/format";

import { changeCounts, sortByChange } from "./compare-view";
import type { DatasetItemRef } from "./experiment-queries";
import { ItemDeltaRow } from "./item-delta-row";
import type { ScoreMeta } from "./score-meta";

/** Items shown before "Show more": the largest changes come first. */
const PAGE = 20;

interface CompareTableProps {
  items: readonly ItemDelta[];
  /** The scores to sort by and the selected one (`?score=`). */
  sort: { scores: readonly ScoreMeta[]; score: ScoreMeta; onChange: (name: string) => void };
  inputs: ReadonlyMap<string, DatasetItemRef> | undefined;
  /** "Run #2" and "Run #3". */
  names: { a: string; b: string };
}

/** Figma "Experiments — Compare" Items: the score selector stays pinned while the list scrolls. */
export function CompareTable({ items, sort, inputs, names }: CompareTableProps) {
  const { scores, score, onChange: onScoreChange } = sort;
  const titleId = useId();
  const [limit, setLimit] = useState(PAGE);
  const sorted = sortByChange(items, score.name);
  const counts = changeCounts(items, score.name);
  const shown = sorted.slice(0, limit);
  const showing =
    shown.length < sorted.length
      ? ` Showing the ${formatInteger(shown.length)} largest changes.`
      : "";

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-3.5 p-5 sm:p-6">
      <div className="sticky top-3 z-10 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[22px] border border-border bg-surface px-3.5 py-2.5 shadow-lg sm:rounded-full">
        <span className="text-label font-semibold text-muted-foreground">Sort by change in</span>
        <SegmentedControl
          aria-label="Sort by change in"
          tone="ink"
          value={score.name}
          onValueChange={onScoreChange}
          options={scores.map((meta) => ({ value: meta.name, label: meta.name }))}
          className="max-w-full overflow-x-auto border-0 bg-surface-muted shadow-none"
        />
        <span className="ml-auto hidden text-xs font-medium text-subtle-foreground sm:inline">
          Pinned while you scroll
        </span>
      </div>
      <div className="flex flex-col">
        <CardTitle id={titleId}>Items</CardTitle>
        <p className="text-xs font-medium text-muted-foreground" aria-live="polite">
          Sorted by change in {score.name}. {pluralize(counts.changed, "item")} changed ·{" "}
          {formatInteger(counts.unchanged)} unchanged · {formatInteger(counts.notComparable)} not
          comparable.{showing}
        </p>
      </div>
      <ul aria-label={`Items sorted by change in ${score.name}`} className="flex flex-col gap-2.5">
        {shown.map((item) => (
          <ItemDeltaRow
            key={item.dataset_item_id}
            item={item}
            score={score}
            input={inputs?.get(item.dataset_item_id)}
            names={names}
          />
        ))}
      </ul>
      {sorted.length > shown.length ? (
        <Button size="sm" className="self-center" onClick={() => setLimit((at) => at + PAGE)}>
          Show {formatInteger(Math.min(PAGE, sorted.length - shown.length))} more
        </Button>
      ) : null}
      <p className="text-xs text-muted-foreground">
        Items that exist in only one run, or have no score on one side, are listed after the matched
        items and show no change.
      </p>
    </Card>
  );
}
