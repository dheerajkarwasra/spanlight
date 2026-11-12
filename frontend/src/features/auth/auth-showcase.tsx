import { LightField, Wires, type LightBand } from "@/features/landing/landing-parts";
import { delay, inViewRef } from "@/features/landing/motion";
import { cn } from "@/lib/utils";

/*
 * The landing hero's worked example (`hero-diagram.tsx`), so both pages tell one story: the demo
 * support bot's `answer_ticket` trace, the cost of its `answer` span and the cause the Doctor
 * found. The three cards are copied here rather than imported, so the auth pages do not load the
 * landing hero; keep the numbers in step with it.
 */
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

/** Trace to cost and trace to cause, from the Figma vectors, in the 840 × 900 stage. */
const WIRE_PATHS = [
  "M420 300C420 250 580 270 580 228",
  "M420 560C420 610 580 590 580 640",
] as const;

/** Figma "Light band" × 2, placed in the panel. */
const BANDS: readonly LightBand[] = [
  {
    left: -218,
    top: 122,
    width: 900,
    height: 200,
    opacity: 0.12,
    rotate: 8,
    blur: 55,
    travel: 120,
    seconds: 9,
  },
  {
    left: 207,
    top: 513,
    width: 900,
    height: 180,
    opacity: 0.1,
    rotate: -6,
    blur: 55,
    travel: -120,
    seconds: 12,
  },
];

/** The cards rise in once on load, 90ms apart. */
const RISE_STAGGER_MS = 90;

const MONO_LABEL = "font-mono text-xs leading-[1.4]";
const CARD = "lp-rise absolute flex flex-col rounded-[14px]";
const DARK_CARD = "border border-mono-800 bg-mono-900";

/**
 * The black panel beside the form on desktop (Figma "Showcase (live figure, decorative)"): the
 * cost, trace and cause cards joined by two wires, over drifting light bands. It is decoration, so
 * it is hidden from assistive technology. Motion follows the landing's rules: the loops run only
 * while the tab is visible, and nothing moves under reduced motion.
 */
export function AuthShowcase({ className }: { className?: string }) {
  return (
    <aside
      ref={inViewRef}
      aria-hidden
      className={cn(
        "auth-showcase sticky top-0 h-dvh min-w-0 flex-1 overflow-clip bg-mono-black select-none",
        className,
      )}
    >
      <LightField bands={BANDS} />

      <div className="auth-stage absolute top-1/2 left-1/2 h-[900px] w-[840px] -translate-1/2">
        <Wires
          paths={WIRE_PATHS}
          width={840}
          height={900}
          stroke="var(--mono-600)"
          pulse="var(--mono-white)"
          drawDelay={RISE_STAGGER_MS * 3}
          pulseDelay={1200}
          className="top-0 left-0"
        />

        <div className={cn(CARD, DARK_CARD, "top-[70px] left-[450px] w-[260px] gap-2.5 p-5")}>
          <span className={cn(MONO_LABEL, "text-mono-400")}>cost · answer span</span>
          <span className="text-[34px] leading-[1.05] font-medium tracking-[-0.035em] text-mono-white tabular-nums">
            {COST_TOTAL}
          </span>
          <div className="flex flex-col gap-1 border-t border-mono-800 pt-2.5 font-mono text-xs leading-normal">
            {COST_LINES.map((line) => (
              <div key={line.label} className="flex justify-between">
                <span className="text-mono-400">{line.label}</span>
                <span className="text-mono-white">{line.cost}</span>
              </div>
            ))}
          </div>
        </div>

        <div
          style={delay(RISE_STAGGER_MS)}
          className={cn(CARD, DARK_CARD, "top-[300px] left-[220px] w-[400px] gap-4 px-6 py-[22px]")}
        >
          <div className={cn(MONO_LABEL, "flex justify-between")}>
            <span className="text-mono-white">trace · answer_ticket</span>
            <span className="text-mono-400">1.42 s</span>
          </div>
          <div className="flex flex-col gap-1">
            {SPANS.map((span) => (
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
                    style={{ left: span.left, width: span.width }}
                    className={cn("absolute top-0 h-2 rounded-[3px]", span.bar)}
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

        <div
          style={delay(RISE_STAGGER_MS * 2)}
          className={cn(
            CARD,
            "top-[640px] left-[450px] w-[260px] items-start gap-3 bg-mono-white p-5",
          )}
        >
          <span className={cn(MONO_LABEL, "text-mono-600")}>cause · tool_loop</span>
          <span className="text-base leading-[1.3] font-medium tracking-[-0.015em] text-mono-black">
            {CAUSE}
          </span>
          <span className="rounded-full bg-mono-black px-2.5 py-1.5 text-xs leading-[1.3] font-medium text-mono-white">
            Suggested fix
          </span>
        </div>

        <p className="absolute top-[780px] left-16 text-[28px] leading-[1.1] font-medium tracking-[-0.03em] whitespace-nowrap text-mono-white">
          Every LLM call, accounted for.
        </p>
        <p className={cn(MONO_LABEL, "absolute top-[826px] left-16 text-mono-400")}>
          Worked example: the demo support bot.
        </p>
      </div>
    </aside>
  );
}
