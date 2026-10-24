import { Link } from "@tanstack/react-router";

import type { ChartDataTable } from "@/components/chart-data-table";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TableRowHeader,
} from "@/components/ui/table";
import { useProjectFilters, useProjectParams } from "@/features/shell/project-context";
import type { Bucket } from "@/lib/api";
import { cn } from "@/lib/utils";

import { bucketSearch } from "./bucket-link";
import { NO_INCIDENT } from "./calls-table";

/** Numbers right-aligned, the partial mark centred; the label and incidents columns read as text. */
const NUMERIC_COLUMNS = new Set([1, 2, 3]);
const PARTIAL_COLUMN = 4;

/**
 * The card's "Show table" view: every bucket with its calls, errors, error rate, partial mark and,
 * when the overlay is on, the overlapping provider incidents. It carries everything the bands show.
 * The box is the only scroller (both axes) and takes keyboard focus, so it can be scrolled without a mouse.
 * Each bucket's label links to its traces: the keyboard twin of clicking a bar.
 */
export function CallsErrorsTable({ table, bucket }: { table: ChartDataTable; bucket: Bucket }) {
  const { orgId, projectId } = useProjectParams();
  const { environment } = useProjectFilters();
  const [labelColumn, ...valueColumns] = table.columns;
  const hasIncidents = table.columns.length > PARTIAL_COLUMN + 1;
  return (
    <div className="flex flex-col gap-2">
      <div
        role="region"
        aria-label={`${table.caption}, table`}
        tabIndex={0}
        className="max-h-[420px] overflow-auto rounded-tile border border-border"
      >
        <Table className="text-xs" containerClassName="overflow-visible">
          <caption className="sr-only">{table.caption}</caption>
          <TableHeader className="sticky top-0 bg-surface">
            <TableRow>
              <TableHead scope="col">{labelColumn}</TableHead>
              {valueColumns.map((column, index) => (
                <TableHead
                  key={column}
                  scope="col"
                  className={cn(
                    NUMERIC_COLUMNS.has(index + 1) && "text-right",
                    index + 1 === PARTIAL_COLUMN && "text-center",
                  )}
                >
                  {column}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {table.rows.map((row) => {
              const [label, ...values] = row.cells;
              return (
                <TableRow key={row.key}>
                  <TableRowHeader scope="row" className="h-10 text-xs">
                    <Link
                      to="/$orgId/$projectId/traces"
                      params={{ orgId, projectId }}
                      search={bucketSearch(row.key, bucket, environment)}
                      className="inline-flex min-h-6 items-center rounded-sm text-accent hover:underline"
                    >
                      {label}
                      <span className="sr-only">: open its traces</span>
                    </Link>
                  </TableRowHeader>
                  {values.map((value, index) => (
                    <TableCell
                      key={table.columns[index + 1] ?? index}
                      className={cn(
                        "h-10",
                        NUMERIC_COLUMNS.has(index + 1) && "text-right tabular",
                        index + 1 === PARTIAL_COLUMN && "text-center",
                        index + 1 > PARTIAL_COLUMN &&
                          cn(
                            "min-w-56 whitespace-normal",
                            value === NO_INCIDENT && "text-muted-foreground",
                          ),
                      )}
                    >
                      {value}
                    </TableCell>
                  ))}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      {hasIncidents ? (
        <p className="text-xs font-medium text-subtle-foreground">
          Incident text comes from the providers’ status pages; “none” means no API-affected
          incident overlapped that {table.columns[0] === "Day" ? "day" : "hour"}.
        </p>
      ) : null}
    </div>
  );
}
