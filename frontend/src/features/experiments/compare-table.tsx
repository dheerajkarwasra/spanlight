import { useId, useState } from "react";

import { Notice } from "@/components/notice";
import { Card, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { Switch } from "@/components/ui/switch";
import type { ItemDelta } from "@/lib/api";
import { formatInteger, pluralize } from "@/lib/format";

import { changeCounts, regressionsOnly, sortByChange } from "./compare-view";
import { ItemDeltaRow } from "./item-delta-row";
import type { ScoreMeta } from "./score-meta";

/** Items shown before "Show more": the largest changes come first. */
const PAGE = 20;

interface CompareTableProps {
  items: readonly ItemDelta[];
  /** The scores to sort by and the selected one (`?score=`). */
  sort: { scores: readonly ScoreMeta[]; score: ScoreMeta; onChange: (name: string) => void };
  /** "Run #2" and "Run #3". */
  names: { a: string; b: string };
  /** Pairs whose item was edited between the runs (`changed_items`), left out of the comparison. */
  edited: number;
}

/** Figma "Experiments — Compare" Items: the score selector stays pinned while the list scrolls. */
export function CompareTable({ items, sort, names, edited }: CompareTableProps) {
  const { scores, score } = sort;
  const titleId = useId();
  const switchId = useId();
  const [limit, setLimit] = useState(PAGE);
  // Spec ruling 6: off by default, not stored.
  const [switchOn, setSwitchOn] = useState(false);
  // A categorical score has no deltas: no switch, and a switch left on for another score is off.
  const canFilter = score.kind !== "categorical";
  const onlyRegressions = switchOn && canFilter;
  const listed = onlyRegressions ? regressionsOnly(items, score.name) : items;
  const sorted = sortByChange(listed, score.name);
  const counts = changeCounts(items, score.name);
  const shown = sorted.slice(0, limit);
  // With the switch on, the pinned bar carries the count; the list line does not repeat it.
  const showing =
    shown.length < sorted.length && !onlyRegressions
      ? ` Showing the ${formatInteger(shown.length)} largest changes.`
      : "";

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-3.5 p-5 sm:p-6">
      <div className="sticky top-3 z-10 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-tile border border-border bg-surface px-3.5 py-2.5 shadow-lg sm:rounded-full">
        <span className="text-label font-semibold text-muted-foreground">Sort by change in</span>
        <SegmentedControl
          aria-label="Sort by change in"
          tone="ink"
          value={score.name}
          onValueChange={(name) => {
            sort.onChange(name);
            setLimit(PAGE);
          }}
          options={scores.map((meta) => ({ value: meta.name, label: meta.name }))}
          className="max-w-full overflow-x-auto border-0 bg-surface-muted shadow-none"
        />
        {canFilter ? (
          <div className="flex items-center gap-2">
            <Switch
              id={switchId}
              checked={onlyRegressions}
              onCheckedChange={(next) => {
                setSwitchOn(next);
                setLimit(PAGE);
              }}
            />
            <Label htmlFor={switchId} className="font-medium whitespace-nowrap">
              Regressions only
            </Label>
          </div>
        ) : null}
        <span className="text-sm font-semibold text-foreground" aria-live="polite">
          {onlyRegressions
            ? `${pluralize(sorted.length, "regression")} of ${pluralize(items.length, "item")}`
            : null}
        </span>
        <span className="ml-auto hidden text-xs font-medium text-subtle-foreground sm:inline">
          Pinned while you scroll
        </span>
      </div>
      <div className="flex flex-col">
        <CardTitle id={titleId}>Items</CardTitle>
        <p className="text-xs font-medium text-muted-foreground">
          Sorted by change in {score.name}. {pluralize(counts.changed, "item")} changed ·{" "}
          {formatInteger(counts.unchanged)} unchanged · {formatInteger(counts.notComparable)} not
          comparable.{showing}
        </p>
      </div>
      {edited > 0 ? (
        <Notice tone="warning">
          {edited === 1
            ? "1 item was edited between the runs and is left out of the comparison."
            : `${formatInteger(edited)} items were edited between the runs and are left out of the comparison.`}
        </Notice>
      ) : null}
      {onlyRegressions && sorted.length === 0 ? (
        <p className="text-sm text-muted-foreground">No item regressed on {score.name}.</p>
      ) : null}
      <ul aria-label={`Items sorted by change in ${score.name}`} className="flex flex-col gap-2.5">
        {shown.map((item) => (
          <ItemDeltaRow key={item.dataset_item_id} item={item} score={score} names={names} />
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
