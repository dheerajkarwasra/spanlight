import { UnknownValue } from "@/components/unknown-value";
import type { CostLine } from "@/lib/api";
import { cn } from "@/lib/utils";

import { formatExactUsd, formatLine } from "./cost-explanation";

/**
 * Figma "Bet1/Cost line row" in a real table: class, tokens, rate, multiplier, USD. Below 640px
 * each row becomes a tile (Figma "Span cost card / mobile 375"): class and USD on top, then
 * "12,310 tokens · $3.00 / MTok · ×1" on one muted line. The unit and separators are CSS-generated
 * so the cells keep their plain values. The explicit table roles keep the semantics that WebKit
 * drops from a table restyled with `display: block` or grid.
 */
export function CostLinesTable({ lines }: { lines: CostLine[] }) {
  return (
    <table role="table" className="w-full border-collapse text-label max-sm:block">
      <caption className="sr-only">Cost lines</caption>
      <thead role="rowgroup" className="max-sm:sr-only">
        <tr role="row" className="text-overline text-subtle-foreground uppercase">
          <th scope="col" role="columnheader" className="px-3 pb-1.5 text-left font-semibold">
            Class
          </th>
          <th scope="col" role="columnheader" className="w-21 pb-1.5 pl-3 text-right font-semibold">
            Tokens
          </th>
          <th
            scope="col"
            role="columnheader"
            className="w-29.5 pb-1.5 pl-3 text-right font-semibold"
          >
            Rate
          </th>
          <th scope="col" role="columnheader" className="w-11 pb-1.5 pl-3 text-right font-semibold">
            <span aria-hidden>×</span>
            <span className="sr-only">Multiplier</span>
          </th>
          <th scope="col" role="columnheader" className="w-24 px-3 pb-1.5 text-right font-semibold">
            USD
          </th>
        </tr>
      </thead>
      <tbody role="rowgroup" className="max-sm:flex max-sm:flex-col max-sm:gap-1.5">
        {lines.map((line) => (
          <CostLineRow key={line.kind} line={line} />
        ))}
      </tbody>
    </table>
  );
}

const MOBILE_PART = "max-sm:row-start-2 max-sm:p-0 max-sm:text-left max-sm:text-xs";
const MOBILE_SEPARATOR = "max-sm:before:mx-1.5 max-sm:before:content-['·']";

function CostLineRow({ line }: { line: CostLine }) {
  const row = formatLine(line);
  const secondary = row.dimmed ? undefined : "text-muted-foreground";
  return (
    <tr
      role="row"
      className={cn(
        "border-b border-border",
        "max-sm:grid max-sm:grid-cols-[auto_auto_auto_minmax(0,1fr)] max-sm:gap-y-0.5",
        "max-sm:rounded-tile max-sm:border-0 max-sm:bg-surface-muted max-sm:px-3 max-sm:py-2.5",
        row.dimmed ? "text-subtle-foreground" : "text-foreground",
      )}
    >
      <th
        scope="row"
        role="rowheader"
        className="px-3 py-2.5 text-left font-medium max-sm:col-span-3 max-sm:p-0 max-sm:text-sm"
      >
        {row.kind}
      </th>
      <td
        role="cell"
        className={cn(
          "pl-3 text-right font-mono tabular",
          MOBILE_PART,
          secondary !== undefined && "max-sm:text-muted-foreground",
          line.unit === "tokens"
            ? "max-sm:after:content-['_tokens']"
            : "max-sm:after:content-['_requests']",
        )}
      >
        {row.quantity}
      </td>
      <td
        role="cell"
        className={cn("pl-3 text-right font-mono", secondary, MOBILE_PART, MOBILE_SEPARATOR)}
      >
        {row.rate}
      </td>
      <td
        role="cell"
        className={cn(
          "pl-3 text-right font-mono",
          secondary,
          MOBILE_PART,
          row.multiplier !== null && MOBILE_SEPARATOR,
        )}
      >
        {row.multiplier}
      </td>
      <td
        role="cell"
        className="px-3 text-right font-mono tabular max-sm:col-start-4 max-sm:row-start-1 max-sm:p-0"
      >
        {row.usd}
      </td>
    </tr>
  );
}

interface CostTotalsProps {
  recomputed: string | null;
  stored: string | null;
}

/** Figma "Totals" and "Stored": the recomputed sum above the figure stored on the span. */
export function CostTotals({ recomputed, stored }: CostTotalsProps) {
  return (
    <dl className="flex flex-col gap-1 px-3 text-label max-sm:px-0">
      <div className="flex items-center gap-3 text-foreground">
        <dt className="flex-1 font-semibold">Recomputed</dt>
        <dd className="text-right font-mono tabular">
          <Amount value={recomputed} reason="Token cost unknown: nothing to recompute" />
        </dd>
      </div>
      <div className="flex items-center gap-3 text-muted-foreground">
        <dt className="flex-1 font-medium">Stored on the span</dt>
        <dd className="text-right font-mono tabular">
          <Amount value={stored} reason="No cost was stored: it is unknown" />
        </dd>
      </div>
    </dl>
  );
}

function Amount({ value, reason }: { value: string | null; reason: string }) {
  const formatted = formatExactUsd(value);
  return formatted === null ? <UnknownValue reason={reason} /> : formatted;
}
