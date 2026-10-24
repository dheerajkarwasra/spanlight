import { Info } from "lucide-react";
import { useState, type ReactNode } from "react";

import { DataTable } from "@/components/data-table";
import { ColumnLabels, TileList } from "@/components/tile-list";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermission, useProjectParams } from "@/features/shell";
import type { Insight } from "@/lib/api";
import { cn } from "@/lib/utils";

import { findingColumns, type FindingSelection } from "./findings-columns";
import { FindingsBulkBar } from "./findings-bulk-bar";
import { InsightRow } from "./insight-row";

interface InsightListProps {
  items: readonly Insight[];
  now: Date;
  /** The "Load more" footer, when there is more. */
  footer?: ReactNode;
}

/**
 * Figma "Doctor list with bulk bar" from `md` up: the findings table, with checkboxes and the
 * bulk bar for `insights:manage`. Phones keep the tiles of "Doctor — Insights list", no bulk.
 */
export function InsightList({ items, now, footer }: InsightListProps) {
  const { orgId, projectId } = useProjectParams();
  const canManage = usePermission("insights:manage");
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  // Only shown insights count as selected; a pick the list no longer shows (status, filters) is
  // dropped at the next change of the selection.
  const selected = items.filter((insight) => picked.has(insight.id));
  const selection: FindingSelection = {
    isSelected: (id) => picked.has(id),
    toggle: (id) => {
      const next = new Set(selected.map((insight) => insight.id));
      if (!next.delete(id)) next.add(id);
      setPicked(next);
    },
    state: selected.length === 0 ? "none" : selected.length === items.length ? "all" : "some",
    toggleAll: () => {
      setPicked(
        selected.length === items.length ? new Set() : new Set(items.map((insight) => insight.id)),
      );
    },
  };

  return (
    <div className="@container flex flex-col gap-3">
      <div
        // While the bar floats over the table's bottom, focused rows scroll clear of it (WCAG
        // 2.4.11): the bar's height plus its bottom offset, whether the table or the page scrolls.
        className={cn(
          selected.length > 0 && "[&_[role=row]]:scroll-mb-24 [&_[role=table]]:scroll-pb-20",
        )}
      >
        <DataTable<Insight>
          id="findings"
          label="Insights"
          columns={findingColumns({
            orgId,
            projectId,
            selection: canManage ? selection : undefined,
          })}
          rows={[...items]}
          rowKey={(insight) => insight.id}
          rowState={(insight) => (picked.has(insight.id) ? "selected" : undefined)}
          rowHref={(insight) => ({
            to: "/$orgId/$projectId/doctor/$insightId",
            params: { orgId, projectId, insightId: insight.id },
          })}
          empty={null}
          loading={false}
        />
      </div>
      <div className="flex flex-col gap-3 md:hidden">
        <ColumnLabels className="gap-5 pr-5 pl-5 @[44rem]:flex">
          <span className="w-[110px] shrink-0">Severity</span>
          <span className="min-w-0 flex-1">Insight</span>
          <span className="w-[250px] shrink-0">Activity</span>
          <span className="w-4 shrink-0" />
        </ColumnLabels>
        <TileList label="Insights">
          {items.map((insight) => (
            <InsightRow key={insight.id} insight={insight} now={now} />
          ))}
        </TileList>
      </div>
      {footer}
      {canManage ? (
        <FindingsBulkBar
          selected={selected}
          keep={(ids) => {
            setPicked(new Set(ids));
          }}
        />
      ) : null}
      <p className="flex items-center gap-2 px-1 pt-1.5 text-xs font-medium text-muted-foreground">
        <Info aria-hidden className="size-3.5 shrink-0" strokeWidth={2} />
        Most recently seen first. A finding resolves on its own after 24 hours without a recurrence.
      </p>
    </div>
  );
}

/** Figma "Doctor — Insights list — loading": tiles shaped like insight rows. */
export function InsightListSkeleton() {
  return (
    <div role="status" aria-label="Loading insights" className="flex flex-col gap-1.5">
      {Array.from({ length: 6 }, (_, index) => (
        <Skeleton key={index} className="h-[82px] rounded-tile" />
      ))}
    </div>
  );
}
