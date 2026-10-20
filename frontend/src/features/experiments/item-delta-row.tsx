import { CircleX, Diff } from "lucide-react";

import { CostValue, UNPRICED_OR_NO_USAGE_REASON } from "@/components/cost-value";
import { UnknownValue } from "@/components/unknown-value";
import type { CompareItemSide, ItemDelta } from "@/lib/api";
import { cn } from "@/lib/utils";

import { formatBooleanChange, formatLatency, formatScoreDelta, itemChange } from "./compare-view";
import {
  EDITED_ITEM_REASON,
  inputText,
  itemLabel,
  itemProblem,
  LEGACY_ITEM_REASON,
} from "./item-copy";
import { ItemScore } from "./item-score";
import { ChangePill } from "./score-chips";
import type { ScoreMeta } from "./score-meta";

interface ItemDeltaRowProps {
  item: ItemDelta;
  /** The score the list is sorted by. */
  score: ScoreMeta;
  names: { a: string; b: string };
}

/** One item of the comparison: how it moved on the selected score, and both answers. */
export function ItemDeltaRow({ item, score, names }: ItemDeltaRowProps) {
  const change = itemChange(item, score.name);
  // A yes / no item reads "No → Yes"; "+100 pts" on one item says nothing more.
  const delta =
    score.kind === "boolean"
      ? formatBooleanChange(item.a?.scores[score.name], item.b?.scores[score.name])
      : formatScoreDelta(item.deltas[score.name] ?? null, score.kind);
  return (
    <li className="flex scroll-mt-24 flex-col gap-2.5 rounded-tile border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="font-mono text-xs text-accent">{itemLabel(item.position)}</span>
        <span className="min-w-0 flex-1 basis-48 text-sm font-semibold text-foreground">
          {/* No copy on either side reads as a legacy run (a known copy pair has `changed` set). */}
          {item.input === null && item.changed === null ? (
            <UnknownValue reason={LEGACY_ITEM_REASON} />
          ) : (
            inputText(item.input)
          )}
        </span>
        <span className="flex items-center gap-2">
          {item.changed ? (
            <>
              <span className="inline-flex min-h-[26px] shrink-0 items-center gap-1.5 rounded-full bg-surface-muted px-2.5 py-1 text-xs font-semibold whitespace-nowrap text-muted-foreground">
                <Diff aria-hidden className="size-3.5" strokeWidth={2.5} />
                Edited between runs
              </span>
              <UnknownValue reason={EDITED_ITEM_REASON} className="text-sm" />
            </>
          ) : (
            <>
              <ChangePill change={change} />
              {delta !== null && (change === "improved" || change === "regressed") ? (
                <span
                  className={cn(
                    "text-sm font-extrabold tabular",
                    change === "improved" ? "text-success" : "text-danger-text",
                  )}
                >
                  {delta}
                </span>
              ) : null}
            </>
          )}
        </span>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <Side side={item.a} name={`A · ${names.a}`} score={score} tone="a" />
        <Side side={item.b} name={`B · ${names.b}`} score={score} tone="b" />
      </div>
    </li>
  );
}

interface SideProps {
  side: CompareItemSide | null;
  name: string;
  score: ScoreMeta;
  tone: "a" | "b";
}

function Side({ side, name, score, tone }: SideProps) {
  const problem = side?.error ? itemProblem(side.error) : null;
  return (
    <div
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-tile p-3.5",
        tone === "a" ? "bg-surface-muted" : "bg-accent-subtle",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={cn(
            "text-xs font-bold",
            tone === "a" ? "text-muted-foreground" : "text-accent-subtle-foreground",
          )}
        >
          {name}
        </span>
        {side && !problem ? (
          <ItemScore meta={score} value={side.scores[score.name]} evaluatorError={null} />
        ) : null}
        {side ? (
          <span className="ml-auto font-mono text-[11px] text-muted-foreground">
            {formatLatency(side.latency_ms) ?? (
              <UnknownValue reason="Unknown: the call was never sent." />
            )}
            {" · "}
            <CostValue cost={side.cost_usd} unknownReason={UNPRICED_OR_NO_USAGE_REASON} />
          </span>
        ) : null}
      </div>
      {side === null ? (
        <p className="text-sm text-muted-foreground">Not in this run.</p>
      ) : problem ? (
        <p className="flex items-start gap-1.5 text-sm text-danger-text">
          <CircleX aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          {problem.text}
        </p>
      ) : (
        <p className="text-[13px] leading-normal font-medium whitespace-pre-line text-foreground">
          {side.output?.text ?? ""}
        </p>
      )}
    </div>
  );
}
