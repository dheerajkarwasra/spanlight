import { CostValue } from "@/components/cost-value";
import { DeltaPill } from "@/components/delta-pill";
import { SectionCard } from "@/components/section-card";
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
import type { Comparison } from "@/lib/api";
import { signedDelta } from "@/lib/delta";
import { cn } from "@/lib/utils";

import { buildKpiRows, NO_COST_REASON, type KpiRow } from "./release-deltas";

const DESCRIPTION = "Candidate (B) against baseline (A). For errors and latency, up is worse.";

type Side = "a" | "b" | "change";

/** Figma "Key metrics": the eight metrics of both releases with the absolute and relative change. */
export function KpiDeltaTable({ comparison }: { comparison: Comparison }) {
  const rows = buildKpiRows(comparison);
  const unpriced = comparison.a.unpriced_calls + comparison.b.unpriced_calls;

  return (
    <SectionCard title="Key metrics" description={DESCRIPTION}>
      <div className="max-sm:hidden">
        <Table aria-label="Key metrics of both releases">
          <TableHeader>
            <TableRow>
              <TableHead>Metric</TableHead>
              <ReleaseHead side="A" release={comparison.a.release} />
              <ReleaseHead side="B" release={comparison.b.release} />
              <TableHead className="text-right">Change</TableHead>
              <TableHead className="text-right">Relative</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.key}>
                <TableRowHeader>{row.label}</TableRowHeader>
                <TableCell className="text-right tabular">
                  <Cell row={row} side="a" />
                </TableCell>
                <TableCell className="text-right font-semibold tabular">
                  <Cell row={row} side="b" />
                </TableCell>
                <TableCell className="text-right tabular">
                  <Cell row={row} side="change" />
                </TableCell>
                <TableCell className="text-right">
                  <Relative row={row} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <ul className="flex flex-col gap-2 sm:hidden">
        {rows.map((row) => (
          <MetricTile key={row.key} row={row} />
        ))}
      </ul>
      {unpriced > 0 ? (
        <p className="text-xs font-medium text-muted-foreground">
          Cost is a lower bound: {unpriced} {unpriced === 1 ? "call has" : "calls have"} no price.
        </p>
      ) : null}
    </SectionCard>
  );
}

function ReleaseHead({ side, release }: { side: "A" | "B"; release: string }) {
  return (
    <TableHead className="text-right">
      <span title={release} className="ml-auto block max-w-40 truncate">
        {side} · {release}
      </span>
    </TableHead>
  );
}

function Cell({ row, side }: { row: KpiRow; side: Side }) {
  if (row.key === "cost_usd" && side !== "change") {
    return (
      <CostValue
        cost={side === "a" ? row.aCost : row.bCost}
        hasUnpriced={side === "a" ? row.aLowerBound : row.bLowerBound}
        unknownReason={NO_COST_REASON}
      />
    );
  }
  const text =
    side === "change"
      ? row.delta === null
        ? null
        : signedDelta(row.delta, () => row.changeMagnitude ?? "—")
      : row[side];
  return text === null ? <UnknownValue reason={row.unknownReason} /> : <>{text}</>;
}

function Relative({ row }: { row: KpiRow }) {
  if (row.delta === null) {
    return <UnknownValue reason={row.unknownReason} />;
  }
  if (row.relativeMagnitude === null) {
    return <UnknownValue reason="The baseline is 0, so a relative change is undefined." />;
  }
  const magnitude = row.relativeMagnitude;
  return (
    <DeltaPill
      delta={row.delta}
      increaseIsGood={row.increaseIsGood}
      format={() => magnitude}
      showArrow
    />
  );
}

function MetricTile({ row }: { row: KpiRow }) {
  return (
    <li className="flex flex-col gap-2 rounded-tile bg-surface-muted p-3.5">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-semibold">{row.label}</span>
        <Relative row={row} />
      </div>
      <dl className="grid grid-cols-3 gap-2 text-sm">
        <TileValue term="A" row={row} side="a" />
        <TileValue term="B" row={row} side="b" bold />
        <TileValue term="Change" row={row} side="change" />
      </dl>
    </li>
  );
}

function TileValue({
  term,
  bold,
  ...cell
}: {
  term: string;
  row: KpiRow;
  side: Side;
  bold?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{term}</dt>
      <dd className={cn("tabular", bold && "font-semibold")}>
        <Cell {...cell} />
      </dd>
    </div>
  );
}
