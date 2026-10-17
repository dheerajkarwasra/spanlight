import { Lock } from "lucide-react";

import { ColumnLabels, TileList } from "@/components/tile-list";
import type { BillingSource } from "@/lib/api";

import { SOURCE_COLUMNS } from "./source-columns";
import { SourceRow } from "./source-row";

interface SourceListProps {
  sources: readonly BillingSource[];
  canManage: boolean;
}

/** Figma "Billing sources / list": tiles under overline column labels, then the schedule note. */
export function SourceList({ sources, canManage }: SourceListProps) {
  return (
    <div className="@container flex flex-col gap-3">
      <ColumnLabels className="pr-3 pl-4 @[40rem]:flex">
        <span className={SOURCE_COLUMNS.name}>Source</span>
        <span className="w-[190px] shrink-0">Status · last success</span>
        <span className="w-[88px] shrink-0">Projects</span>
        {canManage ? <span className="w-[204px] shrink-0" /> : null}
      </ColumnLabels>
      <TileList label="Billing sources">
        {sources.map((source) => (
          <SourceRow key={source.id} source={source} canManage={canManage} />
        ))}
      </TileList>
      <ScheduleNote />
    </div>
  );
}

export function ScheduleNote() {
  return (
    <p className="flex items-start gap-2 px-1 pt-1.5 text-xs font-medium text-subtle-foreground">
      <Lock aria-hidden className="mt-px size-3.5 shrink-0" />
      Sources are fetched every day at 03:30 UTC for the last 7 days. Refresh runs at most once
      every 5 minutes per source. Keys are stored encrypted and never returned.
    </p>
  );
}
