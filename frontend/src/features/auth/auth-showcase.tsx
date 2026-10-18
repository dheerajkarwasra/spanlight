import { cn } from "@/lib/utils";

interface ExampleSpan {
  name: string;
  /** Start and length as a share of the trace, in percent. */
  start: number;
  length: number;
  child: boolean;
  /** The one call the panel is about: lit in lime, like the span in the logo. */
  lit?: boolean;
}

/** The landing hero's example trace (`trace-anatomy.tsx`), so both pages tell one story. */
const EXAMPLE_SPANS: readonly ExampleSpan[] = [
  { name: "answer_ticket", start: 0, length: 100, child: false },
  { name: "search_docs", start: 2, length: 16, child: true },
  { name: "claude-sonnet-5-5 · 429", start: 20, length: 6, child: true },
  { name: "claude-sonnet-5-5", start: 34, length: 58, child: true, lit: true },
  { name: "lookup_order", start: 93, length: 6, child: true },
];

/**
 * The lit call's numbers: 58 % of the 1.42 s trace, and the landing's cost breakdown
 * (input includes the 6,000 cache-read and 1,200 cache-write tokens).
 */
const EXAMPLE_STATS = [
  { label: "Latency", value: "824 ms" },
  { label: "Tokens", value: "9,000 → 420" },
  { label: "Cost", value: "$0.011400" },
] as const;

const INTEGRATIONS = ["Python SDK", "OpenAI", "Anthropic", "OpenTelemetry"] as const;

/**
 * Right half of the desktop auth split screen: one headline and one drawn trace, with the LLM
 * call lit and priced. It is an illustration, so it is hidden from assistive tech and the
 * paragraph above it says what the product does.
 */
export function AuthShowcase({ className }: { className?: string }) {
  return (
    <aside
      aria-labelledby="auth-showcase-title"
      className={cn(
        "relative flex-col gap-12 overflow-hidden rounded-card bg-hero-card p-10 text-hero-card-foreground xl:p-14 dark:border dark:border-border",
        className,
      )}
    >
      <div className="flex flex-1 flex-col justify-center gap-10">
        <div className="flex max-w-[460px] flex-col gap-3.5">
          <p className="text-overline text-rail-muted-foreground uppercase">LLM observability</p>
          <h2 id="auth-showcase-title" className="text-h1 text-rail-foreground">
            Every LLM call, accounted for.
          </h2>
          <p className="text-sm text-rail-muted-foreground">
            Traces, tokens, cost and errors for every request, with each call priced line by line.
          </p>
        </div>

        <TracePreview />
      </div>

      <p className="text-sm text-rail-muted-foreground">
        Works with{" "}
        {INTEGRATIONS.map((name, index) => (
          <span key={name}>
            {index > 0 ? <span className="px-1.5 text-rail-subtle-foreground">·</span> : null}
            <span className="font-medium text-rail-foreground">{name}</span>
          </span>
        ))}
      </p>
    </aside>
  );
}

function TracePreview() {
  return (
    <div aria-hidden className="w-full max-w-[560px] rounded-tile bg-rail-tile">
      <div className="flex items-center justify-between gap-3 px-6 pt-5 pb-3">
        <span className="font-mono text-label text-rail-foreground">answer_ticket</span>
        <span className="text-xs font-medium text-rail-subtle-foreground tabular">
          5 spans · 1.42 s
        </span>
      </div>

      <ul className="flex flex-col gap-1 px-6 pb-5">
        {EXAMPLE_SPANS.map((span) => (
          <li key={span.name} className="flex h-7 items-center gap-4">
            <span
              className={cn(
                "w-[196px] shrink-0 truncate font-mono text-label",
                span.child && "pl-4",
                span.lit ? "text-rail-foreground" : "text-rail-subtle-foreground",
              )}
            >
              {span.name}
            </span>
            <span className="relative h-2 min-w-0 flex-1">
              <span
                className={cn(
                  "absolute inset-y-0 min-w-1.5 rounded-full",
                  span.lit
                    ? "bg-lime shadow-[0_0_16px_color-mix(in_srgb,var(--lime)_45%,transparent)]"
                    : "bg-rail-tile-hover",
                )}
                style={{ left: `${span.start}%`, width: `${span.length}%` }}
              />
            </span>
          </li>
        ))}
      </ul>

      <dl className="grid grid-cols-3 divide-x divide-rail-tile-hover border-t border-rail-tile-hover">
        {EXAMPLE_STATS.map((stat) => (
          <div key={stat.label} className="flex flex-col gap-1 px-6 py-4">
            <dt className="text-overline text-rail-subtle-foreground uppercase">{stat.label}</dt>
            <dd className="text-base font-semibold text-rail-foreground tabular">{stat.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
