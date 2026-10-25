import { DataTable, type DataColumn } from "@/components/data-table";
import { ErrorState } from "@/components/error-state";
import { ReadOnlyNote } from "@/components/read-only-note";
import { SectionCard } from "@/components/section-card";
import { TileList } from "@/components/tile-list";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermission } from "@/features/shell";
import type { Sensitivity } from "@/lib/api";
import { formatInteger, pluralize } from "@/lib/format";

import { useSensitivity } from "./doctor-queries";
import { insightKindLabel } from "./insight-format";
import { SensitivityDialog } from "./sensitivity-dialog";
import { gateText, levelLabel } from "./sensitivity-format";

const LABEL = "Sensitivity per check";

/**
 * Figma "Doctor/Sensitivity": every check with its setting, its findings in the last 30 days and
 * its gate; admins and owners change a setting from the row (spec ruling 9).
 */
export function SensitivityTab() {
  const query = useSensitivity();
  const canManage = usePermission("insights:manage");

  if (query.isPending) {
    return <SensitivitySkeleton />;
  }
  if (query.data === undefined) {
    return (
      <Card>
        <ErrorState
          compact
          error={query.error}
          title="Sensitivity didn’t load"
          retrying={query.isFetching}
          onRetry={() => {
            void query.refetch();
          }}
        />
      </Card>
    );
  }
  const items = query.data.items;
  const adjustable = items.filter((item) => item.tunable).length;
  return (
    <div className="flex flex-col gap-3">
      {canManage ? null : (
        <ReadOnlyNote>Only admins and owners can change sensitivity.</ReadOnlyNote>
      )}
      <SectionCard
        title="How sensitive each check is"
        description="High recall catches every pattern and you dismiss the wrong ones. High precision opens only strong signals and may miss early problems."
        className="gap-4"
      >
        <div className="hidden md:block">
          <DataTable<Sensitivity>
            id="doctor-sensitivity"
            label={LABEL}
            columns={sensitivityColumns(canManage)}
            rows={items}
            rowKey={(item) => item.detector}
            empty="No checks."
            loading={false}
          />
        </div>
        <TileList label={LABEL} className="md:hidden">
          {items.map((item) => (
            <SensitivityTile key={item.detector} item={item} canManage={canManage} />
          ))}
        </TileList>
        <p className="text-xs font-medium text-muted-foreground">
          {pluralize(items.length, "check")} · {formatInteger(adjustable)} adjustable · changes
          apply from the next check run
        </p>
      </SectionCard>
    </div>
  );
}

function name(item: Sensitivity): string {
  return insightKindLabel(item.detector);
}

function SettingPill({ item }: { item: Sensitivity }) {
  if (!item.tunable) {
    return <Badge size="sm">Fixed</Badge>;
  }
  return (
    <Badge size="sm" variant={item.level === "high_precision" ? "ink" : "neutral"}>
      {levelLabel(item.level)}
    </Badge>
  );
}

/** The action column only exists for those who may change a setting (§7ag). */
function sensitivityColumns(canManage: boolean): DataColumn<Sensitivity>[] {
  const columns: DataColumn<Sensitivity>[] = [
    {
      id: "check",
      header: "Check",
      width: "minmax(180px,1.4fr)",
      sticky: true,
      cell: (item) => <span className="font-medium text-foreground">{name(item)}</span>,
    },
    {
      id: "setting",
      header: "Setting",
      width: "140px",
      cell: (item) => <SettingPill item={item} />,
    },
    {
      id: "findings",
      header: "30-day findings",
      width: "150px",
      align: "end",
      cell: (item) => formatInteger(item.findings_30d),
    },
    {
      id: "gate",
      header: "Gate",
      width: "minmax(200px,1.6fr)",
      cell: (item) => gateText(item, item.level),
    },
  ];
  if (canManage) {
    columns.push({
      id: "action",
      header: "Action",
      headerCell: <span className="sr-only">Action</span>,
      width: "96px",
      align: "end",
      cell: (item) => (item.tunable ? <SensitivityDialog item={item} /> : null),
    });
  }
  return columns;
}

/** Figma "Doctor/Sensitivity — 375": one tile per check. */
function SensitivityTile({ item, canManage }: { item: Sensitivity; canManage: boolean }) {
  return (
    <li className="flex flex-col gap-1.5 rounded-tile bg-surface-muted p-3">
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-semibold text-foreground">{name(item)}</span>
        <SettingPill item={item} />
      </div>
      <p className="text-xs font-medium text-muted-foreground tabular">
        {pluralize(item.findings_30d, "finding")} in 30 days · {gateText(item, item.level)}
      </p>
      {canManage && item.tunable ? (
        <div>
          <SensitivityDialog item={item} />
        </div>
      ) : null}
    </li>
  );
}

function SensitivitySkeleton() {
  return (
    <Card
      className="flex flex-col gap-3 p-5 sm:p-6"
      aria-busy="true"
      aria-label="Loading sensitivity"
    >
      <Skeleton className="h-4 w-64" />
      <Skeleton className="h-3 w-full" />
      {[0, 1, 2, 3].map((row) => (
        <Skeleton key={row} className="h-11 rounded-tile" />
      ))}
    </Card>
  );
}
