import { CostValue, UNKNOWN_TOTAL_REASON } from "@/components/cost-value";
import type { DataColumn } from "@/components/data-table";
import { RelativeTime } from "@/components/relative-time";
import { ValueOrUnknown } from "@/components/unknown-value";
import { ScoresCell } from "@/features/scores";
import type { ScoreDataType, TraceSummary } from "@/lib/api";
import { formatDuration } from "@/lib/format";

import { MetadataChips } from "./metadata-chips";
import { InputPreviewCell, OutputPreviewCell } from "./preview-cell";
import { RetryCostCell, RetryCountCell } from "./retry-cell";
import { selectColumn } from "./select-column";
import { ModelBadges, TagBadges, TokenPair, UserSession } from "./trace-cells";
import { TraceName } from "./trace-name";
import type { TraceSelection } from "./use-trace-selection";

type Column = DataColumn<TraceSummary>;

/** The preview columns: the list asks the server for previews only while one of them shows. */
const PREVIEW_COLUMNS = ["input", "output"] as const;

/** Whether Input or Output shows, from the viewer's stored column choices (both on by default). */
export function previewsShown(overrides: Readonly<Record<string, boolean>>): boolean {
  return PREVIEW_COLUMNS.some((id) => overrides[id] ?? true);
}

export interface TraceColumnsContext {
  orgId: string;
  projectId: string;
  selection: TraceSelection | undefined;
  /** Retry cost shows by default while the list is sorted or filtered by retries. */
  retryCost: boolean;
  /** The metadata filter's key: the Metadata column shows, with that key's value first. */
  metadataKey: string | undefined;
  promotedKeys: readonly string[];
  scoreTypeOf: (name: string) => ScoreDataType | null | undefined;
  /** The rows are kept from an earlier request until the new one lands: previews show a placeholder. */
  previewsPending: boolean;
  /** Set from 1280 px: the name link peeks at the trace. */
  onPeek?: (traceId: string) => void;
}

/** The traces table's columns (spec §5 T2, widths from the design critique). */
export function traceColumns(ctx: TraceColumnsContext): Column[] {
  const columns: Column[] = [
    selectColumn(ctx.selection),
    {
      id: "name",
      header: "Name",
      width: "200px",
      sticky: true,
      cell: (trace) => (
        <TraceName trace={trace} orgId={ctx.orgId} projectId={ctx.projectId} onPeek={ctx.onPeek} />
      ),
    },
  ];
  // With promoted keys the viewer can show it from Table settings; a metadata filter shows it.
  if (ctx.metadataKey !== undefined || ctx.promotedKeys.length > 0) {
    const first = ctx.metadataKey;
    const keys =
      first === undefined
        ? ctx.promotedKeys
        : [first, ...ctx.promotedKeys.filter((key) => key !== first)];
    columns.push({
      id: "metadata",
      header: "Metadata",
      width: "180px",
      hideable: true,
      defaultHidden: first === undefined,
      cell: (trace) => <MetadataChips metadata={trace.metadata} promoted={keys} max={1} linked />,
    });
  }
  columns.push(
    {
      id: "input",
      header: "Input",
      width: "minmax(160px,1fr)",
      hideable: true,
      cell: (trace) => <InputPreviewCell trace={trace} pending={ctx.previewsPending} />,
    },
    {
      id: "output",
      header: "Output",
      width: "minmax(160px,1fr)",
      hideable: true,
      cell: (trace) => <OutputPreviewCell trace={trace} pending={ctx.previewsPending} />,
    },
    {
      id: "started",
      header: "Started",
      width: "92px",
      hideable: true,
      sortKey: "started_at",
      cell: (trace) => (
        <RelativeTime iso={trace.started_at} className="truncate text-muted-foreground" />
      ),
    },
    {
      id: "duration",
      header: "Duration",
      width: "96px",
      align: "end",
      hideable: true,
      sortKey: "duration",
      cell: (trace) => (
        <ValueOrUnknown
          value={formatDuration(trace.duration_ms)}
          reason="Duration isn't known yet"
        />
      ),
    },
    {
      id: "model",
      header: "Model",
      width: "150px",
      hideable: true,
      minContainer: 1100,
      cell: (trace) => <ModelBadges models={trace.models} />,
    },
    {
      id: "tokens",
      header: "Tokens",
      width: "104px",
      align: "end",
      hideable: true,
      sortKey: "tokens",
      cell: (trace) => (
        <span className="text-muted-foreground">
          <TokenPair input={trace.input_tokens} output={trace.output_tokens} />
        </span>
      ),
    },
    {
      id: "cost",
      header: "Cost",
      width: "88px",
      align: "end",
      hideable: true,
      sortKey: "cost",
      cell: (trace) => (
        <span className="font-medium">
          <CostValue
            cost={trace.cost_usd}
            hasUnpriced={trace.has_unpriced}
            unknownReason={UNKNOWN_TOTAL_REASON}
          />
        </span>
      ),
    },
    {
      id: "retries",
      header: "Retries",
      width: "80px",
      align: "end",
      hideable: true,
      defaultHidden: true,
      cell: (trace) => <RetryCountCell trace={trace} />,
    },
    {
      id: "retry_cost",
      header: "Retry cost",
      width: "112px",
      align: "end",
      hideable: true,
      defaultHidden: !ctx.retryCost,
      sortKey: "retry_cost_usd",
      cell: (trace) => <RetryCostCell trace={trace} />,
    },
    {
      id: "scores",
      header: "Scores",
      width: "160px",
      hideable: true,
      defaultHidden: true,
      cell: (trace) => (
        <span className="min-w-0 overflow-hidden">
          <ScoresCell scores={trace.scores} typeOf={ctx.scoreTypeOf} />
        </span>
      ),
    },
    {
      id: "tags",
      header: "Tags",
      width: "120px",
      hideable: true,
      defaultHidden: true,
      cell: (trace) => <TagBadges tags={trace.tags} max={1} />,
    },
    {
      id: "user",
      header: "User · session",
      width: "150px",
      hideable: true,
      defaultHidden: true,
      cell: (trace) => <UserSession userId={trace.external_user_id} sessionId={trace.session_id} />,
    },
  );
  return columns;
}
