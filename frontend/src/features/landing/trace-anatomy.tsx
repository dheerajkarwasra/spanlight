import { cn } from "@/lib/utils";

import { Dot, type DotTone, Pill, SpanBar } from "./illustrations/illo-parts";

interface Span {
  name: string;
  dot: DotTone;
  /** Start and length as a share of the trace, in percent. */
  start: number;
  length: number;
  child: boolean;
  selected?: boolean;
}

const SPANS: readonly Span[] = [
  { name: "answer_ticket", dot: "chain", start: 0, length: 100, child: false },
  { name: "search_docs", dot: "retrieval", start: 2, length: 16, child: true },
  { name: "claude-sonnet-5-5 · 429", dot: "danger", start: 20, length: 6, child: true },
  { name: "claude-sonnet-5-5", dot: "llm", start: 34, length: 58, child: true, selected: true },
  { name: "lookup_order", dot: "tool", start: 93, length: 6, child: true },
];

/**
 * claude-sonnet-5-5 in the 2026-10-16 price table, per million tokens: input $2.00, cache read
 * $0.10, 5-minute cache write $2.50, output $10.00. The amounts are those rates applied to the
 * example token counts, so the figure can be checked by hand.
 */
const COST_LINES = [
  { label: "Input", quantity: "1,800 × $2.00/M", amount: "$0.003600" },
  { label: "Cache read", quantity: "6,000 × $0.10/M", amount: "$0.000600" },
  { label: "Cache write · 5 min", quantity: "1,200 × $2.50/M", amount: "$0.003000" },
  { label: "Output", quantity: "420 × $10.00/M", amount: "$0.004200" },
] as const;

const CARD = "flex flex-col rounded-card border border-border bg-surface shadow-card";

/**
 * The hero figure (Figma "Illo/Trace anatomy"): one traced request as a waterfall with the
 * Doctor's finding, beside the cost breakdown of the selected call. It is a drawing, so the
 * figcaption says so and describes it in words; the drawing itself is hidden from assistive tech.
 */
export function TraceAnatomy({ className }: { className?: string }) {
  return (
    <figure className={cn("relative mx-auto max-w-[1180px]", className)}>
      <div aria-hidden className="grid gap-3 text-left lg:grid-cols-[1fr_500px] lg:gap-4">
        <div className={cn(CARD, "gap-2.5 p-4 md:p-6")}>
          <div className="flex items-center gap-2.5 pb-1">
            <span className="text-lg font-extrabold tracking-[-0.02em] text-foreground">
              answer_ticket
            </span>
            <Pill>5 spans · 1.42 s</Pill>
            <Pill mono className="ml-auto hidden md:inline">
              trace
            </Pill>
          </div>
          {SPANS.map((span) => (
            <div
              key={span.name}
              className={cn(
                "flex items-center gap-3 rounded-[10px] px-2.5 py-[7px]",
                span.selected && "bg-accent-subtle",
              )}
            >
              <span
                className={cn(
                  "flex min-w-0 items-center gap-2 md:w-[230px] md:shrink-0",
                  span.child && "pl-4",
                )}
              >
                <Dot tone={span.dot} />
                <span className="truncate font-mono text-xs text-foreground">{span.name}</span>
              </span>
              <span className="relative hidden h-2.5 flex-1 md:block">
                <SpanBar tone={span.dot} start={span.start} length={span.length} />
              </span>
            </div>
          ))}
          <div className="mt-1 flex items-center gap-2.5 rounded-input bg-hero-card px-3.5 py-3 text-hero-card-foreground dark:border dark:border-border">
            <Dot tone="warning" />
            <span className="min-w-0 flex-1 truncate text-label font-semibold">
              Doctor · Retry-After ignored
            </span>
            <Pill tone="lime">Fix</Pill>
            <Pill tone="accent" className="hidden md:inline">
              Check
            </Pill>
          </div>
        </div>

        <div className={cn(CARD, "gap-2 p-4 md:p-6")}>
          <div className="flex items-center justify-between pb-1">
            <span className="text-lg font-extrabold tracking-[-0.02em] text-foreground">
              Cost breakdown
            </span>
            <Pill tone="success">ok</Pill>
          </div>
          {COST_LINES.map((line) => (
            <div
              key={line.label}
              className="flex items-center gap-2.5 rounded-input bg-surface-muted px-3.5 py-2.5"
            >
              <span className="min-w-0 flex-1 truncate text-label font-medium text-foreground">
                {line.label}
              </span>
              <span className="hidden font-mono text-xs text-muted-foreground sm:inline">
                {line.quantity}
              </span>
              <span className="text-label font-semibold text-foreground tabular-nums">
                {line.amount}
              </span>
            </div>
          ))}
          <div className="flex items-center justify-between px-3.5 py-2">
            <span className="text-sm font-bold text-foreground">Total</span>
            <span className="text-base font-extrabold text-foreground tabular-nums">$0.011400</span>
          </div>
          <div className="flex flex-wrap gap-2">
            <Pill mono>anthropic · claude-sonnet-5-5</Pill>
            <Pill mono>table 2026-10-16</Pill>
          </div>
        </div>
      </div>

      <figcaption className="mx-auto mt-4 max-w-[340px] text-center text-xs font-medium text-muted-foreground md:max-w-[720px] lg:mt-6 lg:text-sm">
        An illustration of one traced request: a rate-limited call, its retry and the Doctor’s
        finding, beside the retry’s cost breakdown. Token rates are claude-sonnet-5-5 from the
        2026-10-16 price table.
      </figcaption>
    </figure>
  );
}
