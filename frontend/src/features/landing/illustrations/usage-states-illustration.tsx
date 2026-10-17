import { IlloHeader, IlloNote, IlloRow, Pill } from "./illo-parts";

/**
 * Four spans and the usage state each one carries (Figma "Illo/Usage states"): one priced, the
 * rest unknown with the reason the trace page gives. Drawn on the ink tile.
 */
export function UsageStatesIllustration() {
  return (
    <div className="flex flex-col gap-2">
      <IlloHeader label="Usage state" onInk>
        <Pill tone="lime">Unknown is —, never $0</Pill>
      </IlloHeader>
      <IlloRow tone="rail" dot="llm" label="claude-sonnet-5-5">
        <Pill tone="success" className="hidden md:inline">
          priced
        </Pill>
        <span className="text-sm font-semibold">$0.011400</span>
      </IlloRow>
      <IlloRow tone="rail" dot="chain" label="ai.generateText">
        <Pill tone="accent">
          rollup<span className="hidden md:inline"> · counted once</span>
        </Pill>
      </IlloRow>
      <IlloRow tone="rail" dot="llm" label="stream cut before usage">
        <Pill tone="warning" mono className="hidden md:inline">
          usage_not_reported
        </Pill>
        <span className="text-sm font-semibold text-rail-muted-foreground">—</span>
      </IlloRow>
      <IlloRow tone="rail" dot="llm" label="llama-3.1-8b-instruct">
        <Pill tone="danger" mono className="hidden md:inline">
          no_price
        </Pill>
        <span className="text-sm font-semibold text-rail-muted-foreground">—</span>
      </IlloRow>
      <IlloNote onInk>Each — says why the cost is unknown.</IlloNote>
    </div>
  );
}
