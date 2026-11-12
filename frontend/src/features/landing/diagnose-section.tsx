import { Figure, Section, SectionHeading, TitleSecondLine } from "./landing-parts";
import { inViewRef, stagger } from "./motion";
import { SECTION_IDS } from "./links";

/** The sixteen detector modules in backend/app/insights/detectors, in file order. */
const DETECTORS = [
  "cache_opportunity",
  "client_timeout_misconfigured",
  "context_growth",
  "cost_spike",
  "error_spike",
  "latency_regression",
  "model_retiring",
  "provider_incident",
  "rate_limit_pressure",
  "retry_after_ignored",
  "retry_storm",
  "tool_loop",
  "truncated_outputs",
  "truncated_stream_accepted",
  "unpriced_spend",
  "unsupported_parameter_retried",
] as const;

/** The finding the scan stops on; its title comes from the demo workspace. */
const FINDING = {
  kind: "tool_loop",
  layer: "agent",
  confidence: "Measured",
  title: "The agent repeats `reserve_item` with the same arguments",
  actions: ["Evidence traces", "Suggested fix", "Check it worked"],
} as const;

/**
 * "Diagnose": the detector wall, scanned by one difference-blend highlight that stops on
 * tool_loop, and the finding it raises (Figma "Diagnose"). Phones show the finding first and the
 * detectors as a list.
 */
export function DiagnoseSection() {
  return (
    <Section id={SECTION_IDS.diagnose} tone="black">
      <SectionHeading
        id={SECTION_IDS.diagnose}
        dark
        label="Diagnose"
        title={<TitleSecondLine first="Sixteen detectors." second="No model decides." />}
        description="Fixed rules run over your spans every 15 minutes."
        mobileDescription="Fixed rules run over your spans every 15 minutes. Each finding carries its evidence, a suggested fix and a way to check that it worked."
      />

      <Figure
        label={`The Doctor's sixteen detectors: ${DETECTORS.join(", ")}. A scan stops on tool_loop.`}
        className="relative isolate order-2 grid w-full grid-cols-1 border-t border-mono-800 bg-mono-black xl:order-none xl:grid-cols-4 xl:border-l"
      >
        {DETECTORS.map((detector) => (
          <span
            key={detector}
            className="flex h-[39px] items-center border-b border-mono-800 px-2.5 font-mono text-[13px] leading-[1.4] whitespace-nowrap text-mono-400 xl:h-[92px] xl:border-r xl:px-[22px]"
          >
            {detector}
          </span>
        ))}
        <span className="lp-scan pointer-events-none absolute top-0 left-0 h-[calc(100%/16)] w-full bg-mono-white mix-blend-difference xl:h-1/4 xl:w-1/4" />
      </Figure>

      <div className="order-1 flex w-full flex-col gap-7 xl:order-none xl:w-auto xl:flex-row xl:items-center xl:gap-14">
        <Figure
          label={`A Doctor finding of kind ${FINDING.kind}, layer ${FINDING.layer}, ${FINDING.confidence}: ${FINDING.title}. It offers ${FINDING.actions.join(", ")}.`}
          className="lp-fade flex flex-col gap-5 rounded-xl border border-mono-800 bg-mono-900 px-5 pt-6 pb-5 xl:w-[480px] xl:px-7 xl:pt-7 xl:pb-6"
        >
          <div className="flex items-center justify-between font-mono text-xs leading-[1.4]">
            <span className="flex gap-3">
              <span className="font-medium text-mono-white">
                {FINDING.kind}
                <span className="font-normal xl:hidden"> · {FINDING.layer}</span>
              </span>
              <span className="hidden text-mono-400 xl:inline">layer: {FINDING.layer}</span>
            </span>
            <span className="text-mono-400">{FINDING.confidence}</span>
          </div>
          <span className="text-xl leading-[1.25] font-medium tracking-[-0.02em] text-mono-white xl:text-2xl">
            {FINDING.title}
          </span>
          <div className="flex flex-wrap gap-2">
            {FINDING.actions.map((action) => (
              <span
                key={action}
                className="rounded-full border border-mono-600 px-3 py-2 text-[13px] leading-[1.3] font-medium text-mono-white"
              >
                {action}
              </span>
            ))}
          </div>
          <div className="hidden items-center gap-3.5 border-t border-mono-800 pt-[18px] text-[13px] leading-[1.4] xl:flex">
            <span className="text-mono-400">Was this right?</span>
            <span className="font-medium text-mono-white">Yes</span>
            <span className="font-medium text-mono-white">No</span>
          </div>
        </Figure>
        <p
          ref={inViewRef}
          style={stagger(2)}
          className="lp-view lp-rise hidden w-[400px] text-[17px] leading-[1.55] text-mono-400 xl:block"
        >
          Each finding carries its evidence, a suggested fix and a way to check that it worked.
          Provider health tells an outage or a rate limit apart from your own bug.
        </p>
      </div>

      <p className="order-3 text-sm leading-[1.55] text-mono-400 xl:hidden">
        Provider health tells an outage or a rate limit apart from your own bug, with the provider’s
        status page incidents alongside.
      </p>
    </Section>
  );
}
