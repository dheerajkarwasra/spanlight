import type { ChartDataTable } from "@/components/chart-data-table";
import { UnknownValue } from "@/components/unknown-value";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TableRowHeader,
} from "@/components/ui/table";

const NOT_REVIEWED = "No reviewed traces in this release and window";

/**
 * Figma "Failure categories card / table": the chart's "Show table" view and its 375 px form,
 * every release × category as `count (rate, interval)`. The box is the only scroller (both axes)
 * and takes keyboard focus, so it can be scrolled without a mouse.
 */
export function FailureCategoriesTable({ table }: { table: ChartDataTable }) {
  const [first, ...rest] = table.columns;
  return (
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
            <TableHead scope="col">{first}</TableHead>
            {rest.map((column) => (
              <TableHead key={column} scope="col" className="text-right">
                {column}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {table.rows.map((row) => {
            const [release, ...cells] = row.cells;
            return (
              <TableRow key={row.key}>
                <TableRowHeader scope="row" className="h-10 text-xs whitespace-nowrap">
                  {release}
                </TableRowHeader>
                {cells.map((cell, index) => (
                  <TableCell
                    key={rest[index] ?? index}
                    className="h-10 text-right whitespace-nowrap tabular"
                  >
                    {cell === "—" ? <UnknownValue reason={NOT_REVIEWED} /> : cell}
                  </TableCell>
                ))}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
