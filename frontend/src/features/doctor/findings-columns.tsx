import { Link } from "@tanstack/react-router";

import type { DataColumn } from "@/components/data-table";
import { RelativeTime } from "@/components/relative-time";
import { Tooltip } from "@/components/ui/tooltip";
import { RowCheckbox } from "@/features/traces";
import type { Insight } from "@/lib/api";

import { formatDateTime, LAYER_LABELS, STATUS_LABELS } from "./insight-format";
import { KindChip } from "./kind-chip";
import { SeverityBadge } from "./severity-badge";

type Column = DataColumn<Insight>;

/** The table's full width: every track with the select column (870 px) plus the row padding. */
const FULL_TRACKS = 910;

/** The ticked insights; the select column exists only with `insights:manage`. */
export interface FindingSelection {
  isSelected: (id: string) => boolean;
  toggle: (id: string) => void;
  /** "all" when every shown insight is ticked, "some" when part of them, "none" otherwise. */
  state: "all" | "some" | "none";
  toggleAll: () => void;
}

/** "Open", "Acknowledged", "Muted until Oct 28, 09:00", "Resolved". */
function statusText(insight: Insight): string {
  const until = insight.status === "muted" ? formatDateTime(insight.muted_until) : null;
  return until === null ? STATUS_LABELS[insight.status] : `Muted until ${until}`;
}

/** The Doctor list's columns (spec §4.2, Figma "Doctor list with bulk bar"). */
export function findingColumns(ctx: {
  orgId: string;
  projectId: string;
  selection: FindingSelection | undefined;
}): Column[] {
  const { selection } = ctx;
  const columns: Column[] = [];
  if (selection) {
    columns.push({
      id: "select",
      header: "Select",
      headerCell: (
        <span className="-mx-1 flex">
          <RowCheckbox
            checked={selection.state === "some" ? "indeterminate" : selection.state === "all"}
            onCheckedChange={selection.toggleAll}
            label="Select every insight shown"
          />
        </span>
      ),
      width: "40px",
      sticky: true,
      cell: (insight) => (
        <span className="-mx-1 flex">
          <RowCheckbox
            checked={selection.isSelected(insight.id)}
            onCheckedChange={() => {
              selection.toggle(insight.id);
            }}
            label={`Select ${insight.title}`}
          />
        </span>
      ),
    });
  }
  columns.push(
    {
      id: "finding",
      header: "Finding",
      width: "minmax(220px,1fr)",
      sticky: true,
      cell: (insight) => (
        <span className="flex min-w-0 items-center gap-2">
          <SeverityBadge severity={insight.severity} />
          <Link
            to="/$orgId/$projectId/doctor/$insightId"
            params={{ orgId: ctx.orgId, projectId: ctx.projectId, insightId: insight.id }}
            title={insight.title}
            className="truncate font-medium text-foreground hover:underline"
          >
            {insight.title}
          </Link>
        </span>
      ),
    },
    {
      id: "kind",
      header: "Kind",
      width: "150px",
      hideable: true,
      cell: (insight) => <KindChip kind={insight.kind} label={insight.label} />,
    },
    {
      id: "layer",
      header: "Layer",
      width: "90px",
      // Below every track's width, the first columns to give way (Table settings shows them).
      minContainer: FULL_TRACKS,
      hideable: true,
      cell: (insight) => LAYER_LABELS[insight.failure_layer],
    },
    {
      id: "occurrences",
      header: "Occurrences",
      headerCell: (
        <Tooltip content="Detector runs that saw this problem">
          <button type="button" className="min-h-6 rounded-sm uppercase hover:text-foreground">
            Occurrences
          </button>
        </Tooltip>
      ),
      width: "110px",
      minContainer: FULL_TRACKS,
      align: "end",
      hideable: true,
      cell: (insight) => insight.occurrences,
    },
    {
      id: "last_seen",
      header: "Last seen",
      width: "110px",
      align: "end",
      hideable: true,
      cell: (insight) => (
        <RelativeTime iso={insight.last_seen_at} className="truncate text-muted-foreground" />
      ),
    },
    {
      id: "status",
      header: "Status",
      width: "150px",
      hideable: true,
      cell: (insight) => <span className="truncate">{statusText(insight)}</span>,
    },
  );
  return columns;
}
