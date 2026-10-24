import type { ReactNode } from "react";

import type { DataColumn } from "@/components/data-table";
import { UnknownValue } from "@/components/unknown-value";
import { formatInteger } from "@/lib/format";

import { RETRIES_NOT_COMPUTED } from "./attribution-grid";
import { ErrorCount, GroupCost, RetryCost } from "./attribution-row";
import type { DrillContext } from "./drill-down";
import { GroupCell, ShareOrWhole, UnpricedCell } from "./spend-cells";
import type { SpendRow } from "./spend-rows";

/** The table's full width: every default track (828 px) plus the Table settings track. */
const FULL_TRACKS = 868;

interface ColumnContext {
  drill: DrillContext;
  onFocus: (value: string) => void;
}

/**
 * Spec §3.3: Group (sticky) · Traces · LLM calls · Errors · Unpriced · Cost · Share · Retries and
 * Retry cost (hidden by default). The row menu sits in the Group cell. Sort keys are the API's `sort` values.
 */
export function spendColumns({ drill, onFocus }: ColumnContext): DataColumn<SpendRow>[] {
  const columns: DataColumn<SpendRow>[] = [
    {
      id: "group",
      header: "Group",
      width: "minmax(200px,1fr)",
      sticky: true,
      cell: (row) => <GroupCell row={row} drill={drill} onFocus={onFocus} />,
    },
    {
      id: "traces",
      header: "Traces",
      width: "80px",
      align: "end",
      cell: (row) => formatInteger(row.figures.traces),
    },
    {
      id: "calls",
      header: "LLM calls",
      width: "96px",
      // Below every track's width, the first columns to give way (Table settings shows them).
      minContainer: FULL_TRACKS,
      hideable: true,
      align: "end",
      sortKey: "calls",
      cell: (row) => formatInteger(row.figures.llm_calls),
    },
    {
      id: "errors",
      header: "Errors",
      width: "80px",
      minContainer: FULL_TRACKS,
      hideable: true,
      align: "end",
      sortKey: "errors",
      cell: (row) => <ErrorCount count={row.figures.errors} />,
    },
    {
      id: "unpriced",
      header: "Unpriced",
      width: "136px",
      align: "end",
      cell: (row) => <UnpricedCell count={row.figures.unpriced_calls} />,
    },
    {
      id: "cost",
      header: "Cost",
      width: "112px",
      align: "end",
      sortKey: "cost",
      cell: (row) => <GroupCost figures={row.figures} />,
    },
    {
      id: "share",
      header: "Share",
      width: "124px",
      cell: (row) => <ShareOrWhole row={row} />,
    },
    {
      id: "retries",
      header: "Retries",
      width: "88px",
      align: "end",
      hideable: true,
      defaultHidden: true,
      cell: (row) =>
        row.figures.retries === null ? (
          <UnknownValue reason={RETRIES_NOT_COMPUTED} />
        ) : (
          formatInteger(row.figures.retries)
        ),
    },
    {
      id: "retry_cost",
      header: "Retry cost",
      width: "112px",
      align: "end",
      hideable: true,
      defaultHidden: true,
      sortKey: "retry_cost",
      cell: (row) => <RetryCost figures={row.figures} />,
    },
  ];
  // The total row reads as the table's sum.
  return columns.map((col) => ({
    ...col,
    cell: (row): ReactNode =>
      row.kind === "total" ? <span className="font-semibold">{col.cell(row)}</span> : col.cell(row),
  }));
}
