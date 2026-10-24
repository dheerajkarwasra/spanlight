import { useNavigate } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { DataTable, type SortOrder } from "@/components/data-table";
import { useScoreTypeOf } from "@/features/scores";
import { useProjectParams, useProjectQuery } from "@/features/shell/project-context";
import type { TraceSummary } from "@/lib/api";

import { retryCostColumn } from "./retry-format";
import { TRACE_SORTS, type TraceFilters } from "./search";
import { traceColumns } from "./traces-columns";
import type { TraceSelection } from "./use-trace-selection";

const NO_KEYS: readonly string[] = [];

interface TracesTableProps {
  traces: TraceSummary[];
  /** The URL search: the sort, and the filters that add columns (retries, metadata). */
  search: TraceFilters;
  onSortChange: (sort: TraceFilters["sort"], order: SortOrder | undefined) => void;
  /** The trace opened last; its row is highlighted when you come back to the list. */
  selectedTraceId?: string | null;
  /** First load (skeleton rows), or results for new filters on their way (rows dim). */
  loading: boolean;
  /** Ticked rows for the bulk actions; without it the checkbox column stays empty. */
  selection?: TraceSelection;
  /** From 1280 px: a row click (or a plain click on its name) peeks instead of navigating. */
  onPeek?: (traceId: string) => void;
  footer?: ReactNode;
}

/** The traces list from `md` up: the shared DataTable with the trace columns (spec §5). */
export function TracesTable({
  traces,
  search,
  onSortChange,
  selectedTraceId = null,
  loading,
  selection,
  onPeek,
  footer,
}: TracesTableProps) {
  const navigate = useNavigate();
  const { orgId, projectId } = useProjectParams();
  const promotedKeys = useProjectQuery().data?.promoted_metadata_keys ?? NO_KEYS;
  const scoreTypeOf = useScoreTypeOf();
  const columns = traceColumns({
    orgId,
    projectId,
    selection,
    retryCost: retryCostColumn(search) !== "hidden",
    metadataKey: search.metadata_key,
    promotedKeys,
    scoreTypeOf,
    previewsPending: loading,
    onPeek,
  });

  // The one place a row click is handled (links, buttons and selected text are left alone).
  function openTrace(trace: TraceSummary) {
    if (onPeek) {
      onPeek(trace.trace_id);
      return;
    }
    void navigate({
      to: "/$orgId/$projectId/traces/$traceId",
      params: { orgId, projectId, traceId: trace.trace_id },
    });
  }

  return (
    <DataTable<TraceSummary>
      id="traces"
      label="Traces"
      columns={columns}
      rows={traces}
      rowKey={(trace) => trace.trace_id}
      onRowClick={openTrace}
      rowState={(trace) =>
        trace.trace_id === selectedTraceId || selection?.isSelected(trace.trace_id)
          ? "selected"
          : undefined
      }
      sort={{
        key: search.sort ?? "started_at",
        order: search.order ?? "desc",
        onChange: (key, order) => {
          const sort = TRACE_SORTS.find((candidate) => candidate === key);
          // Descending is the default order, so the URL leaves it out; newest first has no sort either.
          if (sort === "started_at" && order === "desc") onSortChange(undefined, undefined);
          else onSortChange(sort, order === "desc" ? undefined : order);
        },
      }}
      footer={footer}
      empty={null}
      loading={loading}
    />
  );
}
