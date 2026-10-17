import { Info } from "lucide-react";
import type { ReactNode } from "react";

import { Tooltip } from "@/components/ui/tooltip";
import type { AttributionBucket } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

import { ATTRIBUTION_GRID, CAP_REASON, type AttributionView } from "./attribution-grid";
import { DrillLink, FigureCells, VALUE_LINK_CLASS } from "./attribution-row";
import { drillDownSearch, type DrillContext } from "./drill-down";
import { noneText, shareOf } from "./footer-format";

/** The none label: the key in mono; a link to the traces without the key when the list can filter so. */
export function NoneLabel({ drill, linked }: { drill: DrillContext; linked: boolean }) {
  const search = linked ? drillDownSearch(drill, null) : null;
  const text =
    drill.groupBy === "metadata" ? (
      <span className="truncate font-sans">
        No <span className="font-mono">{drill.key}</span>
      </span>
    ) : (
      <span className="truncate font-sans">{noneText(drill)}</span>
    );
  if (search === null) {
    return <span className="flex min-w-0 font-mono text-code">{text}</span>;
  }
  return (
    <DrillLink search={search} className={VALUE_LINK_CLASS}>
      {text}
    </DrillLink>
  );
}

/**
 * "Up to 212 more values, daily cap of 1,000" with the cap explained; never a link. The count
 * sums each UTC day's folded values, so a value folded on several days counts more than once: it
 * is an upper bound.
 */
export function OtherLabel({ other }: { other: AttributionBucket }) {
  const folded = other.folded_values;
  const count = folded === null ? null : formatInteger(folded);
  const noun = folded === 1 ? "value" : "values";
  const text =
    count === null
      ? "More values, daily cap of 1,000"
      : `Up to ${count} more ${noun}, daily cap of 1,000`;
  const why = count === null ? "Why values are folded" : `Why up to ${count} ${noun} are folded`;
  return (
    <span className="flex min-w-0 items-center gap-1 font-sans text-sm text-muted-foreground">
      <span className="truncate">{text}</span>
      <Tooltip content={CAP_REASON}>
        <button
          type="button"
          aria-label={why}
          // 24 px target; a 44 px hit area on phones (RULING T20-3).
          className="relative inline-flex size-6 shrink-0 items-center justify-center rounded-full text-subtle-foreground before:absolute before:-inset-2.5 hover:text-foreground md:before:hidden"
        >
          <Info aria-hidden className="size-3.5" />
        </button>
      </Tooltip>
    </span>
  );
}

function FooterRow({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div role="row" className={cn(ATTRIBUTION_GRID, "h-[52px] text-sm", className)}>
      {children}
    </div>
  );
}

/** The table's last rows: the traces without the dimension, the folded values and the total. */
export function AttributionFooterRows({ view }: { view: AttributionView }) {
  const { none, other, totals } = view.page;
  return (
    <div role="rowgroup" className="flex flex-col gap-0.5 border-t border-border pt-0.5">
      {none ? (
        <FooterRow>
          <div role="cell" className="flex min-w-0">
            <NoneLabel drill={view.drill} linked />
          </div>
          <FigureCells figures={none} share={shareOf(none, view)} />
        </FooterRow>
      ) : null}
      {other ? (
        <FooterRow>
          <div role="cell" className="flex min-w-0">
            <OtherLabel other={other} />
          </div>
          <FigureCells figures={other} share={shareOf(other, view)} />
        </FooterRow>
      ) : null}
      <FooterRow className="border-t border-border font-semibold">
        <div role="rowheader">Total</div>
        <FigureCells figures={totals} share="total" />
      </FooterRow>
    </div>
  );
}
