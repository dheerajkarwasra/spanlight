import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { ProviderHealth } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { rateLimitKindLabel } from "@/lib/rate-limit-kind";

import { formatRatio, kindRows, type KindRow } from "./providers-format";

/** Phones list this many kinds before "Show all". */
const PHONE_ROWS = 4;

/**
 * Figma "Rate-limit kinds card": why providers refused calls (429s and overloaded 5xx), per
 * provider, with each kind's share of that provider's rows and how many carried a retry-after.
 */
export function RateLimitKindsTable({ providers }: { providers: readonly ProviderHealth[] }) {
  const titleId = useId();
  const rows = kindRows(providers);
  const [showAll, setShowAll] = useState(false);
  const phoneRows = showAll ? rows : rows.slice(0, PHONE_ROWS);
  const hasSpendCap = rows.some((row) => row.kind === "spend_cap");

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-4 p-5 sm:p-6">
      <div className="flex flex-col gap-1">
        <CardTitle id={titleId}>Rate-limit kinds</CardTitle>
        <p className="text-xs font-medium text-muted-foreground">
          Why providers refused calls: 429s and overloaded 5xx
          <span className="max-sm:hidden">
            , read from the provider’s error type, code, message, then its rate-limit headers
          </span>
          .
        </p>
      </div>
      {rows.length === 0 ? (
        <p className="rounded-tile bg-surface-muted px-4 py-6 text-center text-sm text-muted-foreground">
          No 429s or overloaded errors in this window
        </p>
      ) : (
        <>
          <Table className="max-sm:hidden">
            <TableHeader>
              <TableRow>
                <TableHead>Provider</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Code</TableHead>
                <TableHead className="text-right">Count</TableHead>
                <TableHead className="text-right">Share</TableHead>
                <TableHead className="text-right">With retry-after</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={`${row.provider ?? ""}:${row.kind}`}>
                  <TableCell className="h-12 font-mono text-[13px] text-muted-foreground">
                    {row.provider ?? "No provider"}
                  </TableCell>
                  <TableCell className="h-12">{rateLimitKindLabel(row.kind)}</TableCell>
                  <TableCell className="h-12 font-mono text-[13px] text-muted-foreground">
                    {row.kind}
                  </TableCell>
                  <TableCell className="h-12 text-right tabular">
                    {formatInteger(row.count)}
                  </TableCell>
                  <TableCell className="h-12 text-right tabular">
                    {formatRatio(row.share, 1)}
                  </TableCell>
                  <TableCell className="h-12 text-right tabular">{retryAfterText(row)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <ul aria-label="Rate-limit kinds" className="flex flex-col gap-1.5 sm:hidden">
            {phoneRows.map((row) => (
              <KindTile key={`${row.provider ?? ""}:${row.kind}`} row={row} />
            ))}
          </ul>
          {rows.length > PHONE_ROWS && !showAll ? (
            <Button
              className="self-start sm:hidden"
              onClick={() => {
                setShowAll(true);
              }}
            >
              Show all {rows.length} kinds
            </Button>
          ) : null}
        </>
      )}
      <p className="text-xs font-medium text-subtle-foreground max-sm:hidden">
        Share is of each provider’s rows in this table (429s and overloaded 5xx). The cards’ 429
        share counts provider 429s only, divided by failed calls.
        {hasSpendCap ? "" : " Spend cap: none in this window."} Gateway key limit is Spanlight’s own
        per-key limit, never counted as provider pressure.
      </p>
    </Card>
  );
}

function retryAfterText(row: KindRow): string {
  return `${formatInteger(row.withRetryAfter) ?? "0"} · ${formatRatio(row.retryShare, 1) ?? "—"}`;
}

function KindTile({ row }: { row: KindRow }) {
  return (
    <li className="flex flex-col gap-1 rounded-tile bg-surface-muted px-3.5 py-3">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-semibold">{rateLimitKindLabel(row.kind)}</span>
        <span className="font-mono text-[13px] text-muted-foreground">
          {row.provider ?? "No provider"}
        </span>
      </div>
      <p className="text-xs font-medium text-muted-foreground">
        {row.kind} · {formatInteger(row.count)} {row.count === 1 ? "refusal" : "refusals"} ·{" "}
        {formatRatio(row.share, 1)} · retry-after {retryAfterText(row)}
      </p>
    </li>
  );
}
