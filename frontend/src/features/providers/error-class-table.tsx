import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TableRowHeader,
} from "@/components/ui/table";
import type { Bucket, ErrorClass } from "@/lib/api";
import { errorClassLabel } from "@/lib/error-class";
import { formatInteger } from "@/lib/format";

import { bucketRange } from "./bucket-labels";
import type { StackedPoint } from "./error-series";

interface ErrorClassTableProps {
  points: StackedPoint[];
  classes: ErrorClass[];
  bucket: Bucket;
}

/**
 * The chart's "Show table" view: every bucket × class, the same counts as the bars. The box is
 * the only scroller (both axes) and takes keyboard focus, so it can be scrolled without a mouse.
 */
export function ErrorClassTable({ points, classes, bucket }: ErrorClassTableProps) {
  const noun = bucket === "hour" ? "Hour (UTC)" : "Day (UTC)";
  return (
    <div
      role="region"
      aria-label={`Failed LLM calls per ${bucket} and class, table`}
      tabIndex={0}
      className="max-h-[420px] overflow-auto rounded-tile border border-border"
    >
      <Table className="text-xs" containerClassName="overflow-visible">
        <caption className="sr-only">Failed LLM calls per {bucket} and class</caption>
        <TableHeader className="sticky top-0 bg-surface">
          <TableRow>
            <TableHead scope="col">{noun}</TableHead>
            {classes.map((name) => (
              <TableHead key={name} scope="col" className="text-right">
                {errorClassLabel(name)}
              </TableHead>
            ))}
            <TableHead scope="col" className="text-right">
              Total
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {points.map((point) => (
            <TableRow key={point.bucketStart}>
              <TableRowHeader scope="row" className="h-10 text-xs">
                {bucketRange(point.bucketStart, bucket)}
              </TableRowHeader>
              {classes.map((name) => (
                <TableCell key={name} className="h-10 text-right tabular">
                  {formatInteger(point.counts[name] ?? 0)}
                </TableCell>
              ))}
              <TableCell className="h-10 text-right font-semibold tabular">
                {formatInteger(point.total)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
