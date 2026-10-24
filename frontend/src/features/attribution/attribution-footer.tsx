import { Info } from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";
import type { AttributionBucket } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { CAP_REASON } from "./attribution-grid";
import { DrillLink, VALUE_LINK_CLASS } from "./attribution-row";
import { drillDownSearch, type DrillContext } from "./drill-down";
import { noneText } from "./footer-format";

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
