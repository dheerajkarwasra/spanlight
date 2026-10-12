import { ArrowDown, ArrowRight, ArrowUp, Minus } from "lucide-react";
import type { ReactNode } from "react";

import { CostValue, UNPRICED_OR_NO_USAGE_REASON } from "@/components/cost-value";
import { UnknownValue } from "@/components/unknown-value";
import type { RunComparison } from "@/lib/api";
import { formatInteger, parseMoney } from "@/lib/format";
import { cn } from "@/lib/utils";

import { sideReason, type RunSide, type SidePair } from "./compare-sides";
import {
  costCertainty,
  deltaTone,
  formatLatency,
  formatMoneyDelta,
  formatMsDelta,
  type CostCertainty,
} from "./compare-view";

const NO_LATENCY = "Unknown: this run has no item that answered.";

interface Change {
  /** `b - a`; null when either side is unknown. */
  delta: number | null;
  /** "+$0.012", "+310 ms", "+2". */
  text: string | null;
  /** "costs more" / "costs less", "slower" / "faster", "more errors" / "fewer errors". */
  words: [string, string];
  /** A cost from lower-bound totals says "at least", or that it is partly unknown. */
  certainty?: CostCertainty;
}

function ChangeBadge({ change }: { change: Change }) {
  const { delta, text, words, certainty = "exact" } = change;
  if (text === null) {
    return <UnknownValue reason="Unknown: one side has no value to compare." />;
  }
  if (certainty === "partial") {
    return (
      <UnknownValue reason="Partly unknown: run A has calls without a price, so its cost is only a lower bound." />
    );
  }
  const tone = deltaTone(delta, false);
  const Icon = tone === "neutral" ? Minus : delta !== null && delta > 0 ? ArrowUp : ArrowDown;
  const word = delta !== null && delta > 0 ? words[0] : words[1];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-bold",
        tone === "bad" && "bg-danger-subtle text-danger-text",
        tone === "good" && "bg-success-subtle text-success",
        tone === "neutral" && "bg-surface-muted text-muted-foreground",
      )}
    >
      <Icon aria-hidden className="size-3" strokeWidth={2.5} />
      {tone === "neutral"
        ? "No change"
        : `${certainty === "at_least" ? "at least " : ""}${text} ${word}`}
    </span>
  );
}

interface TileProps {
  label: string;
  before: ReactNode;
  after: ReactNode;
  change: Change;
}

function Tile({ label, before, after, change }: TileProps) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-tile border border-border bg-surface p-4">
      <dt className="text-overline text-subtle-foreground uppercase">{label}</dt>
      <dd className="flex flex-wrap items-center gap-2 font-mono">
        <span className="text-xs text-muted-foreground">{before}</span>
        <ArrowRight aria-hidden className="size-3 text-muted-foreground" />
        <span className="sr-only">to</span>
        <span className="text-sm font-bold text-foreground">{after}</span>
      </dd>
      <dd>
        <ChangeBadge change={change} />
      </dd>
    </div>
  );
}

function cost(side: RunSide) {
  return side.totals ? (
    <CostValue
      cost={side.totals.cost_usd}
      hasUnpriced={side.totals.has_unpriced}
      unknownReason={UNPRICED_OR_NO_USAGE_REASON}
    />
  ) : (
    <UnknownValue reason={sideReason(side, UNPRICED_OR_NO_USAGE_REASON)} />
  );
}

function latency(side: RunSide, ms: number | null | undefined) {
  return formatLatency(ms ?? null) ?? <UnknownValue reason={sideReason(side, NO_LATENCY)} />;
}

/** Figma "Experiments — Compare" cost and latency tiles: A → B with the change; lower is better. */
export function CompareTiles({ pair, comparison }: { pair: SidePair; comparison: RunComparison }) {
  const { a, b } = pair;
  const p50 =
    a.totals?.latency_p50_ms != null && b.totals?.latency_p50_ms != null
      ? b.totals.latency_p50_ms - a.totals.latency_p50_ms
      : null;
  const errors = a.totals && b.totals ? b.totals.errors - a.totals.errors : null;
  const errorCount = (side: RunSide) =>
    formatInteger(side.totals?.errors) ?? <UnknownValue reason={sideReason(side, "Unknown.")} />;
  return (
    <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Tile
        label="Cost"
        before={cost(a)}
        after={cost(b)}
        change={{
          delta: parseMoney(comparison.cost_delta_usd),
          text: formatMoneyDelta(comparison.cost_delta_usd),
          words: ["costs more", "costs less"],
          certainty: costCertainty(
            a.totals?.has_unpriced ?? false,
            b.totals?.has_unpriced ?? false,
          ),
        }}
      />
      <Tile
        label="Latency p50"
        before={latency(a, a.totals?.latency_p50_ms)}
        after={latency(b, b.totals?.latency_p50_ms)}
        change={{ delta: p50, text: formatMsDelta(p50), words: ["slower", "faster"] }}
      />
      <Tile
        label="Latency p95"
        before={latency(a, a.totals?.latency_p95_ms)}
        after={latency(b, b.totals?.latency_p95_ms)}
        change={{
          delta: comparison.latency_p95_delta_ms,
          text: formatMsDelta(comparison.latency_p95_delta_ms),
          words: ["slower", "faster"],
        }}
      />
      <Tile
        label="Errors"
        before={errorCount(a)}
        after={errorCount(b)}
        change={{
          delta: errors,
          text: errors === null ? null : `${errors > 0 ? "+" : "−"}${Math.abs(errors)}`,
          words: ["more errors", "fewer errors"],
        }}
      />
    </dl>
  );
}
