import { CostValue } from "@/components/cost-value";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TableRowHeader,
} from "@/components/ui/table";
import { formatInteger } from "@/lib/format";

import type { SpendBar } from "./spend-series";

const NO_BUCKET_PRICE = "No priced calls in this bucket";

/** The chart's "Show table" view: every bucket with its spend and unpriced calls, the keyboard path. */
export function SpendTable({ bars, per }: { bars: SpendBar[]; per: string }) {
  return (
    <div
      role="region"
      aria-label={`Spend ${per}, table`}
      tabIndex={0}
      className="max-h-[420px] overflow-auto rounded-tile border border-border"
    >
      <Table className="text-xs" containerClassName="overflow-visible">
        <caption className="sr-only">Spend and unpriced calls {per}</caption>
        <TableHeader className="sticky top-0 bg-surface">
          <TableRow>
            <TableHead scope="col">Start</TableHead>
            <TableHead scope="col" className="text-right">
              Spend
            </TableHead>
            <TableHead scope="col" className="text-right">
              Unpriced calls
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {bars.map((bar) => (
            <TableRow key={bar.start}>
              <TableRowHeader scope="row" className="h-10 text-xs">
                {bar.label}
              </TableRowHeader>
              <TableCell className="h-10 text-right">
                <CostValue
                  cost={bar.cost}
                  hasUnpriced={bar.lowerBound}
                  unknownReason={NO_BUCKET_PRICE}
                />
              </TableCell>
              <TableCell className="h-10 text-right tabular">
                {formatInteger(bar.unpriced)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
