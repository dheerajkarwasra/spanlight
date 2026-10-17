import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TableRowHeader,
} from "@/components/ui/table";
import { UnknownValue } from "@/components/unknown-value";
import type { PrecisionKind } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

import { formatPrecisionRow, type PrecisionRowView } from "./precision-format";
import { PrecisionAxis, PrecisionInterval } from "./precision-interval";

export const NO_PRECISION_REASON = "No answers yet, so no precision";

/** "Meets target" / "Below target" from 20 answers on; the note ("not enough feedback yet") under. */
export function PrecisionStatusMark({ view }: { view: PrecisionRowView }) {
  if (view.status === "meets") {
    return <Badge variant="success">Meets target</Badge>;
  }
  if (view.status === "below") {
    return <Badge variant="warning">Below target</Badge>;
  }
  return <span className="text-xs font-medium text-muted-foreground">{view.note}</span>;
}

/** The precision to two places, or "—" with the reason when nobody answered. */
export function PrecisionValue({ view }: { view: PrecisionRowView }) {
  return view.precision === null ? <UnknownValue reason={NO_PRECISION_REASON} /> : view.precision;
}

function count(value: number): string {
  return formatInteger(value) ?? String(value);
}

/**
 * Figma "Doctor/Precision" table (640 px and up): one row per detector kind with its answers,
 * precision and the Wilson 95 % interval against the target line. Rows under 20 answers are
 * greyed and never marked.
 */
export function PrecisionTable({ rows }: { rows: readonly PrecisionKind[] }) {
  return (
    <Table>
      <caption className="sr-only">
        Precision per check from members’ answers, with the Wilson 95&nbsp;% interval
      </caption>
      <TableHeader>
        <TableRow className="hover:[&>td]:bg-transparent">
          <TableHead>Check</TableHead>
          <TableHead className="text-right">Right</TableHead>
          <TableHead className="text-right">Wrong</TableHead>
          <TableHead className="text-right">N</TableHead>
          <TableHead className="text-right">Precision</TableHead>
          <TableHead className="w-[28%] align-bottom">
            <span className="block pb-1">Wilson 95&nbsp;%</span>
            <PrecisionAxis />
          </TableHead>
          <TableHead>
            <span className="sr-only">Status</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <PrecisionTableRow key={row.kind} row={row} />
        ))}
      </TableBody>
    </Table>
  );
}

function PrecisionTableRow({ row }: { row: PrecisionKind }) {
  const view = formatPrecisionRow(row);
  const marked = view.status === "meets" || view.status === "below";
  const numbers = cn("text-right tabular", marked ? "text-foreground" : "text-muted-foreground");
  return (
    <TableRow>
      <TableRowHeader className="h-16 whitespace-normal">{row.label}</TableRowHeader>
      <TableCell className={numbers}>{count(row.right)}</TableCell>
      <TableCell className={numbers}>{count(row.wrong)}</TableCell>
      <TableCell className={numbers}>{count(row.n)}</TableCell>
      <TableCell className={cn(numbers, marked && "font-semibold")}>
        <PrecisionValue view={view} />
      </TableCell>
      <TableCell>
        <PrecisionInterval bar={view.bar} marked={marked} />
        <span className="sr-only">{view.interval ?? "No interval"}</span>
      </TableCell>
      <TableCell>
        <PrecisionStatusMark view={view} />
      </TableCell>
    </TableRow>
  );
}
