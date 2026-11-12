import type { CSSProperties } from "react";

import { CodeSampleCard } from "./code-sample-card";
import { Section, SectionHeading } from "./landing-parts";
import { inViewRef } from "./motion";
import { SECTION_IDS } from "./links";

/** Providers and OTLP conventions Spanlight reads as sent. */
const STACK = [
  "OpenAI",
  "Anthropic",
  "OpenTelemetry",
  "OpenInference",
  "Vercel AI SDK",
  "OpenLLMetry",
  "LiteLLM",
] as const;

/** One pass of the marquee at about 40px a second. */
const MARQUEE_SECONDS = 32;

/** "Start": the three ways in with their code, and what Spanlight reads (Figma "Start"). */
export function StartSection() {
  return (
    <Section id={SECTION_IDS.start} tone="white">
      <SectionHeading
        id={SECTION_IDS.start}
        label="Start"
        title="Send your first trace."
        description="The Python SDK, any OTLP exporter, or the gateway."
        mobileDescription={null}
      />
      <CodeSampleCard />
      <div
        ref={inViewRef}
        className="flex w-full flex-col gap-3 border-t border-mono-line-strong pt-7 xl:flex-row xl:items-center xl:gap-14 xl:py-7"
      >
        <p className="text-[13px] leading-[1.4] text-mono-500 xl:w-[250px] xl:shrink-0 xl:text-sm">
          Reads what your stack already sends
        </p>
        <p className="text-[17px] leading-[1.5] font-medium tracking-[-0.015em] text-mono-600 xl:hidden">
          {STACK.join(" · ")}
        </p>
        {/* The second copy only closes the loop; a paused marquee is a static, wrapped list. */}
        <div className="hidden min-w-0 flex-1 overflow-hidden xl:block">
          <ul
            style={{ "--lp-dur": `${MARQUEE_SECONDS}s` } as CSSProperties}
            className="lp-loop lp-marquee flex w-max text-[22px] leading-[1.3] font-medium tracking-[-0.015em] whitespace-nowrap text-mono-600"
          >
            {[...STACK, ...STACK].map((name, index) => (
              <li key={index} aria-hidden={index >= STACK.length} className="pr-14">
                {name}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Section>
  );
}
