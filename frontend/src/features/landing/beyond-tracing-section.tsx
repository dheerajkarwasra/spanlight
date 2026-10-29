import { FeatureTile } from "./feature-tile";
import { EvalsIllustration } from "./illustrations/evals-illustration";
import { GatewayIllustration } from "./illustrations/gateway-illustration";
import { PromptsIllustration } from "./illustrations/prompts-illustration";
import { SessionsIllustration } from "./illustrations/sessions-illustration";
import { SECTION_IDS } from "./links";
import { SectionHeading, TitleAccent } from "./section-heading";

/** Four supporting capabilities in a two-column grid, stacked on phones. */
export function BeyondTracingSection() {
  return (
    <section
      id={SECTION_IDS.diagnose}
      aria-labelledby="diagnose-title"
      className="scroll-mt-6 px-5 pt-16 md:px-8 lg:pt-24"
    >
      <div id={SECTION_IDS.features} className="scroll-mt-6">
        <SectionHeading
          id="diagnose-title"
          overline="Build on your traces"
          title={
            <>
              Improve what <TitleAccent>happens next.</TitleAccent>
            </>
          }
          description="Route requests, test quality, iterate on prompts and follow complete conversations."
        />
      </div>
      <div className="mx-auto mt-9 grid max-w-[1200px] grid-cols-1 gap-4 md:grid-cols-2 lg:mt-10 xl:gap-5">
        <FeatureTile
          overline="Gateway"
          title="One base URL, no SDK"
          description="Use one base URL for tracing, retries, fallbacks, caching and budgets."
          illustration={<GatewayIllustration />}
          className="min-h-[469px] md:min-h-[458px]"
        />
        <FeatureTile
          overline="Evals"
          title="Evals with honest error bars"
          description="Compare runs, score answers and catch quality regressions before release."
          illustration={<EvalsIllustration />}
          className="min-h-[440px] md:min-h-[458px]"
        />
        <FeatureTile
          overline="Prompts"
          title="Prompts, playground, replay"
          description="Version prompts, test them side by side and replay recorded calls."
          illustration={<PromptsIllustration />}
          className="min-h-[440px] md:min-h-[484px]"
        />
        <FeatureTile
          overline="Sessions"
          title="Sessions"
          description="Group multi-turn conversations and replay them as chat, with cost and latency for every turn."
          illustration={<SessionsIllustration />}
          className="min-h-[495px] md:min-h-[484px]"
        />
      </div>
    </section>
  );
}
