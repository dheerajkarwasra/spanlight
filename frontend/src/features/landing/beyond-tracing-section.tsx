import { FeatureTile } from "./feature-tile";
import { DoctorIllustration } from "./illustrations/doctor-illustration";
import { EvalsIllustration } from "./illustrations/evals-illustration";
import { GatewayIllustration } from "./illustrations/gateway-illustration";
import { PromptsIllustration } from "./illustrations/prompts-illustration";
import { ReviewIllustration } from "./illustrations/review-illustration";
import { SECTION_IDS } from "./links";
import { SectionHeading, TitleAccent } from "./section-heading";

/**
 * What Spanlight does after a trace lands (Figma "Section/Beyond tracing", v4: "Diagnose and
 * improve"): the Doctor and provider health, the gateway, prompts and replay, evals, and the
 * daily review. Same bento grid as the features section: 7+5 then 4+5+3 from 1280px.
 */
export function BeyondTracingSection() {
  return (
    <section
      id={SECTION_IDS.diagnose}
      aria-labelledby="diagnose-title"
      className="scroll-mt-6 px-5 pt-24 md:px-8 lg:pt-40"
    >
      <SectionHeading
        id="diagnose-title"
        overline="Diagnose and improve"
        title={
          <>
            Find the problem, prove the fix,{" "}
            <TitleAccent>
              <span className="whitespace-nowrap">keep it fixed.</span>
            </TitleAccent>
          </>
        }
        description="Tracing shows what happened. The Doctor, the gateway, evals and a daily review tell you what to do next, on the same Postgres."
      />

      <div className="mx-auto mt-9 grid max-w-[1200px] grid-cols-1 gap-4 md:grid-cols-2 lg:mt-14 xl:grid-cols-12 xl:gap-5">
        <FeatureTile
          tone="ink"
          overline="Doctor"
          title="A doctor for your LLM calls"
          description={{
            mobile:
              "Fifteen detectors open an insight with evidence, a fix and a way to check it worked. Provider health tells an outage from your own bug.",
            desktop:
              "Fifteen deterministic detectors read your traces and open an insight with the evidence, a suggested fix and a way to check that it worked. Provider health tells a provider’s outage or limit apart from your own bug, and every insight can be marked right or wrong.",
          }}
          illustration={<DoctorIllustration />}
          className="md:col-span-2 xl:col-span-7"
        />
        <FeatureTile
          overline="Gateway"
          title="One base URL, no SDK"
          description={{
            mobile:
              "Point your OpenAI or Anthropic client at the gateway: traced, priced, retried, cached and budgeted.",
            desktop:
              "Point the OpenAI or Anthropic client at the gateway and every call is traced and priced, with retries, fallbacks, caching and budgets that block. The Integration Lab replays real provider failures so you can test your client.",
          }}
          illustration={<GatewayIllustration />}
          className="md:col-span-2 xl:col-span-5"
        />
        <FeatureTile
          overline="Prompts"
          title="Prompts, playground, replay"
          description="Version prompts, move labels like production, try them side by side, and replay any recorded call against the provider."
          illustration={<PromptsIllustration />}
          className="xl:col-span-4"
        />
        <FeatureTile
          overline="Evals"
          title="Evals with honest error bars"
          description={{
            mobile:
              "Score answers, compare two runs with a significance test and fail the pull request when quality drops.",
            desktop:
              "Score answers with rules, LLM judges on a budget or your own endpoint, compare two runs with a confidence interval and a significance test, and fail the pull request when quality drops.",
          }}
          illustration={<EvalsIllustration />}
          className="xl:col-span-5"
        />
        <FeatureTile
          overline="Review"
          title="A daily review queue"
          description="Each night, yesterday’s traces worth reading are queued: Doctor findings, errors, cost outliers and a random sample."
          illustration={<ReviewIllustration />}
          className="md:col-span-2 xl:col-span-3"
        />
      </div>
    </section>
  );
}
