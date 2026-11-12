import { cn } from "@/lib/utils";

import { Wires } from "./landing-parts";
import { delay } from "./motion";

/*
 * Worked example, labelled as such under the figure: the demo support bot's `answer_ticket`
 * trace, with the `answer` span priced at claude-sonnet-5-5 list rates ($2.00 / $10.00 / $0.10
 * per 1M input, output and cache-read tokens). Span bar positions are an illustration.
 */
const CALLS = [
  { model: "claude-sonnet-5-5", priced: true },
  { model: "claude-haiku-5-5", priced: true },
  { model: "gpt-4o", priced: true },
  { model: "gpt-4.1-mini", priced: true },
  { model: "llama-3.1-8b-instruct", priced: false },
] as const;

/** Bar offsets and widths on a 120px track. */
const SPANS = [
  {
    kind: "agent",
    name: "answer_ticket",
    left: 0,
    width: 120,
    duration: "1.42 s",
    bar: "bg-mono-600",
  },
  { kind: "llm", name: "triage", left: 0, width: 26, duration: "0.31 s", bar: "bg-mono-400" },
  {
    kind: "tool",
    name: "help_search",
    left: 26,
    width: 14,
    duration: "0.16 s",
    bar: "bg-mono-400",
  },
  { kind: "llm", name: "answer", left: 40, width: 80, duration: "0.95 s", bar: "bg-mono-white" },
] as const;

const LIT_SPAN = "answer";

const COST_TOTAL = "$0.008420";
const COST_LINES = [
  { label: "input", cost: "$0.003680" },
  { label: "cache read", cost: "$0.000620" },
  { label: "output", cost: "$0.004120" },
] as const;

const CAUSE = "The agent repeats `reserve_item` with the same arguments";

/** Wire paths from the Figma vectors, in a 161.5 × 560 and a 141.5 × 560 box. */
const CALL_WIRES = [
  "M0.75 80C80.75 80 80.75 200 160.75 200",
  "M0.75 168C80.75 168 80.75 230 160.75 230",
  "M0.75 256C80.75 256 80.75 260 160.75 260",
  "M0.75 344C80.75 344 80.75 290 160.75 290",
  "M0.75 432C80.75 432 80.75 320 160.75 320",
] as const;

const ANSWER_WIRES = [
  "M0.75 230C70.75 230 70.75 150 140.75 150",
  "M0.75 330C70.75 330 70.75 420 140.75 420",
] as const;

const WIRE = "var(--mono-800)";
const PULSE = "var(--mono-white)";
const MONO_LABEL = "font-mono text-xs leading-[1.4]";

/**
 * The hero's live diagram on desktop (Figma "Live diagram · calls → trace → cost and cause"):
 * model calls slide in, wires draw and carry light pulses, the trace card assembles its spans,
 * then the cost card and the lit cause card arrive. The build-up is timed from page load.
 */
export function HeroDiagram() {
  return (
    <div className="relative hidden h-[560px] w-[1200px] xl:block">
      {CALLS.map((call, index) => (
        <div
          key={call.model}
          style={{ top: 58 + index * 88, ...delay(600 + index * 90) }}
          className="lp-slide absolute left-0 flex h-11 w-[240px] items-center gap-2.5 rounded-[10px] border border-mono-800 bg-mono-black px-3.5"
        >
          <span
            className={cn("size-2 rounded-full", call.priced ? "bg-mono-white" : "bg-mono-600")}
          />
          <span className={cn(MONO_LABEL, "text-mono-white")}>{call.model}</span>
        </div>
      ))}

      <Wires
        paths={CALL_WIRES}
        width={161.5}
        height={560}
        stroke={WIRE}
        pulse={PULSE}
        drawDelay={1000}
        pulseDelay={2200}
        className="top-0 left-[239.25px]"
      />

      <div
        style={delay(1500)}
        className="lp-rise absolute top-[90px] left-[400px] flex w-[400px] flex-col gap-4 rounded-[14px] border border-mono-800 bg-mono-900 px-6 py-[22px]"
      >
        <div className={cn(MONO_LABEL, "flex justify-between")}>
          <span className="text-mono-white">trace · answer_ticket</span>
          <span className="text-mono-400">1.42 s</span>
        </div>
        <div className="flex flex-col gap-1">
          {SPANS.map((span, index) => (
            <div
              key={span.name}
              className={cn(
                MONO_LABEL,
                "flex items-center gap-2 rounded-lg p-2.5",
                span.name === LIT_SPAN && "bg-mono-800",
              )}
            >
              <span className="w-[34px] text-mono-400">{span.kind}</span>
              <span className="w-[98px] text-mono-white">{span.name}</span>
              <span className="relative h-2 w-[120px]">
                <span
                  style={{ left: span.left, width: span.width, ...delay(1900 + index * 90) }}
                  className={cn("lp-grow absolute top-0 h-2 rounded-[3px]", span.bar)}
                />
              </span>
              <span className="flex-1 text-right text-mono-400">{span.duration}</span>
            </div>
          ))}
        </div>
        <div className={cn(MONO_LABEL, "flex justify-between border-t border-mono-800 pt-3.5")}>
          <span className="text-mono-400">prompt · completion · tokens · cost</span>
          <span className="text-mono-white">live</span>
        </div>
      </div>

      <Wires
        paths={ANSWER_WIRES}
        width={141.5}
        height={560}
        stroke={WIRE}
        pulse={PULSE}
        drawDelay={2600}
        pulseDelay={3800}
        className="top-0 left-[799.25px]"
      />

      <div
        style={delay(3000)}
        className="lp-rise absolute top-10 left-[940px] flex w-[260px] flex-col gap-2.5 rounded-[14px] border border-mono-800 bg-mono-900 p-5"
      >
        <span className={cn(MONO_LABEL, "text-mono-400")}>cost · answer span</span>
        <span className="text-[34px] leading-[1.05] font-medium tracking-[-0.035em] text-mono-white tabular-nums">
          {COST_TOTAL}
        </span>
        <div className="flex flex-col gap-1 border-t border-mono-800 pt-2.5 font-mono text-xs leading-[1.5]">
          {COST_LINES.map((line, index) => (
            <div
              key={line.label}
              style={delay(3200 + index * 120)}
              className="lp-fade flex justify-between"
            >
              <span className="text-mono-400">{line.label}</span>
              <span className="text-mono-white">{line.cost}</span>
            </div>
          ))}
        </div>
      </div>

      <div
        style={delay(3500)}
        className="lp-rise absolute top-[320px] left-[940px] flex w-[260px] flex-col items-start gap-3 rounded-[14px] bg-mono-white p-5"
      >
        <span className={cn(MONO_LABEL, "text-mono-600")}>cause · tool_loop</span>
        <span className="text-base leading-[1.3] font-medium tracking-[-0.015em] text-mono-black">
          {CAUSE}
        </span>
        <span className="rounded-full bg-mono-black px-2.5 py-1.5 text-xs leading-[1.3] font-medium text-mono-white">
          Suggested fix
        </span>
      </div>
    </div>
  );
}

/** The same story on phones (Figma mobile "Hero"): two calls, the trace, its cost and the cause. */
export function HeroDiagramMobile() {
  return (
    <div className="flex flex-col xl:hidden">
      <div style={delay(600)} className="lp-rise flex gap-2">
        {CALLS.filter((call) => call.model === "claude-sonnet-5-5" || call.model === "gpt-4o").map(
          (call) => (
            <div
              key={call.model}
              className="flex h-10 min-w-0 flex-1 items-center gap-2.5 rounded-[10px] border border-mono-800 bg-mono-black px-3"
            >
              <span className="size-2 shrink-0 rounded-full bg-mono-white" />
              <span className={cn(MONO_LABEL, "truncate text-mono-white")}>{call.model}</span>
            </div>
          ),
        )}
      </div>
      <span className="mx-auto h-9 w-px bg-mono-600" />
      <div
        style={delay(800)}
        className="lp-rise flex flex-col gap-3 rounded-[14px] border border-mono-800 bg-mono-900 px-4 py-[18px]"
      >
        <div className={cn(MONO_LABEL, "flex justify-between")}>
          <span className="text-mono-white">trace · answer_ticket</span>
          <span className="text-mono-400">1.42 s</span>
        </div>
        {SPANS.map((span, index) => (
          <div key={span.name} className={cn(MONO_LABEL, "flex items-center justify-between")}>
            <span className="text-mono-white">{span.name}</span>
            <span className="relative h-2 w-[120px]">
              <span
                style={{ left: span.left, width: span.width, ...delay(1100 + index * 90) }}
                className={cn("lp-grow absolute top-0 h-2 rounded-[3px]", span.bar)}
              />
            </span>
          </div>
        ))}
      </div>
      <span className="mx-auto h-9 w-px bg-mono-600" />
      <div
        style={delay(1500)}
        className="lp-rise flex items-center justify-between rounded-[14px] border border-mono-800 bg-mono-900 px-4 py-3.5"
      >
        <span className={cn(MONO_LABEL, "text-mono-400")}>cost · answer span</span>
        <span className="text-2xl leading-[1.05] font-medium tracking-[-0.035em] text-mono-white tabular-nums">
          {COST_TOTAL}
        </span>
      </div>
      <div
        style={delay(1700)}
        className="lp-rise mt-2 flex flex-col gap-1.5 rounded-[14px] bg-mono-white px-4 py-3.5"
      >
        <span className={cn(MONO_LABEL, "text-mono-600")}>cause · tool_loop</span>
        <span className="text-[15px] leading-[1.3] font-medium tracking-[-0.015em] text-mono-black">
          {CAUSE}
        </span>
      </div>
    </div>
  );
}
