import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import type { AttributionTotals } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

import { NoneLabel, OtherLabel } from "./attribution-footer";
import { shareOf } from "./footer-format";
import { dimensionName, type AttributionView } from "./attribution-grid";
import { DrillLink, ErrorCount, GroupCost, ShareCell, ValueLabel } from "./attribution-row";
import { drillDownSearch, type TraceDrillSearch } from "./drill-down";

const TILE_CLASS =
  "flex flex-col gap-2 rounded-tile bg-surface-muted px-4 py-3.5 transition-colors";

interface TileProps {
  label: ReactNode;
  figures: AttributionTotals;
  share: number | null;
  /** The whole tile is the link (RULING T20-3); no link for "other" and the total. */
  search: TraceDrillSearch | null;
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="flex flex-col gap-0.5">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-foreground tabular">{children}</span>
    </span>
  );
}

/** Figma "Attribution / Page (375)" tile: value and cost, the share bar, calls, errors, traces. */
function AttributionTile({ label, figures, share, search }: TileProps) {
  const body = (
    <>
      <span className="flex min-w-0 items-baseline justify-between gap-3">
        <span className={cn("flex min-w-0 font-mono text-code", search && "text-accent")}>
          {label}
        </span>
        <span className="shrink-0 text-base font-semibold text-foreground">
          <GroupCost figures={figures} plain />
        </span>
      </span>
      <ShareCell share={share} track="w-[120px]" plain />
      <span className="grid grid-cols-[repeat(3,minmax(0,72px))] gap-3 text-xs font-medium">
        <Stat label="LLM calls">{formatInteger(figures.llm_calls)}</Stat>
        <Stat label="Errors">
          <ErrorCount count={figures.errors} />
        </Stat>
        <Stat label="Traces">{formatInteger(figures.traces)}</Stat>
      </span>
    </>
  );
  if (search === null) {
    return <div className={TILE_CLASS}>{body}</div>;
  }
  return (
    <DrillLink search={search} className={cn(TILE_CLASS, "hover:bg-surface-hover")}>
      {body}
    </DrillLink>
  );
}

/** The phone layout: one tile per value. */
export function AttributionTiles({ view, stale }: { view: AttributionView; stale: boolean }) {
  return (
    <ul
      aria-label={`Cost by ${dimensionName(view.drill.groupBy, view.drill.key)}`}
      aria-busy={stale || undefined}
      className={cn("flex flex-col gap-2 transition-opacity", stale && "opacity-60")}
    >
      {view.items.map((row) => (
        <li key={row.value}>
          <AttributionTile
            label={<ValueLabel value={row.value} />}
            figures={row}
            share={row.share_of_cost}
            search={drillDownSearch(view.drill, row.value)}
          />
        </li>
      ))}
    </ul>
  );
}

/** The stacked footer at 375: none, other and the total under "Outside the list". */
export function AttributionFooterTiles({ view }: { view: AttributionView }) {
  const { none, other, totals } = view.page;
  return (
    <section aria-label="Outside the list" className="flex flex-col gap-2">
      {none || other ? (
        <h2 className="text-overline text-muted-foreground uppercase">Outside the list</h2>
      ) : null}
      {none ? (
        <AttributionTile
          label={<NoneLabel drill={view.drill} linked={false} />}
          figures={none}
          share={shareOf(none, view)}
          search={drillDownSearch(view.drill, null)}
        />
      ) : null}
      {other ? (
        <AttributionTile
          label={<OtherLabel other={other} />}
          figures={other}
          share={shareOf(other, view)}
          search={null}
        />
      ) : null}
      <div className={TILE_CLASS}>
        <span className="flex items-baseline justify-between gap-3 font-semibold text-foreground">
          <span className="text-sm">Total</span>
          <span className="text-base">
            <GroupCost figures={totals} />
          </span>
        </span>
        <span className="grid grid-cols-[repeat(3,minmax(0,72px))] gap-3 text-xs font-medium">
          <Stat label="LLM calls">{formatInteger(totals.llm_calls)}</Stat>
          <Stat label="Errors">
            <ErrorCount count={totals.errors} />
          </Stat>
          <Stat label="Traces">{formatInteger(totals.traces)}</Stat>
        </span>
      </div>
    </section>
  );
}

export function AttributionTilesSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div aria-hidden className="flex flex-col gap-2">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className={cn(TILE_CLASS, "gap-3")}>
          <span className="flex justify-between">
            <Skeleton className="h-4 w-28" />
            <Skeleton className="h-4 w-14" />
          </span>
          <Skeleton className="h-3 w-40" />
        </div>
      ))}
    </div>
  );
}
