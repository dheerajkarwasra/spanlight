import { FeatureTile } from "./feature-tile";
import { LandingShot } from "./landing-shot";
import { SECTION_IDS } from "./links";
import { SectionHeading, TitleAccent } from "./section-heading";

/** Screenshot frame inside a tile: tile radius and a hairline so a light screen keeps its edge. */
const SHOT = "rounded-tile border border-border";
const INK_SHOT = "rounded-tile border border-rail-tile";

/**
 * What Spanlight does after a trace lands (Figma "Section/Beyond tracing"): the Doctor, the
 * gateway, prompts, evals, alerts and budgets, each with a real screenshot from the live demo.
 * Same bento grid as the features section: 7+5 then 4+5+3 from 1280px.
 */
export function BeyondTracingSection() {
  return (
    <section
      id={SECTION_IDS.beyondTracing}
      aria-labelledby="beyond-tracing-title"
      className="scroll-mt-6 px-5 pt-24 md:px-8 lg:pt-40"
    >
      <SectionHeading
        id="beyond-tracing-title"
        overline="Beyond tracing"
        title={
          <>
            Find the problem, prove the fix,{" "}
            <TitleAccent>
              <span className="whitespace-nowrap">cap the spend.</span>
            </TitleAccent>
          </>
        }
        description="Tracing shows what happened. The Doctor, evals and the gateway tell you what to do next, on the same Postgres."
      />

      <div className="mx-auto mt-9 grid max-w-[1200px] grid-cols-1 gap-4 md:grid-cols-2 lg:mt-14 xl:grid-cols-12 xl:gap-5">
        <FeatureTile
          tone="ink"
          overline="Doctor"
          title="A doctor for your LLM calls"
          description={{
            mobile:
              "Fifteen deterministic detectors read your traces and open an insight with evidence, a fix and a way to check that it worked.",
            desktop:
              "Fifteen deterministic detectors read your traces and open an insight when something wastes money, time or correctness, like a retry storm or an ignored Retry-After. Each one carries the evidence traces, a suggested fix and a way to check that the fix worked.",
          }}
          illustration={<LandingShot shot="doctor" className={INK_SHOT} />}
          illustrationIsContent
          className="md:col-span-2 xl:col-span-7 xl:min-h-[507px]"
        />
        <FeatureTile
          overline="Gateway"
          title="One base URL, no SDK"
          description={{
            mobile:
              "Point your OpenAI or Anthropic client at the gateway: traced, priced, retried, cached and budgeted, with a Lab for real failures.",
            desktop:
              "Point the OpenAI or Anthropic client at the gateway and every call is traced and priced, with retries, fallbacks, caching and budgets that block. The Integration Lab replays real provider failures so you can test your client.",
          }}
          illustration={<LandingShot shot="gateway" className={SHOT} />}
          illustrationIsContent
          className="xl:col-span-5 xl:min-h-[507px]"
        />
        <FeatureTile
          overline="Prompts"
          title="Prompts and playground"
          description={{
            mobile:
              "Version prompts, move labels like production and see which version produced every trace.",
            desktop:
              "Version prompts, move labels like production, try them in the playground, and see which version produced every trace.",
          }}
          illustration={<LandingShot shot="prompts" className={SHOT} />}
          illustrationIsContent
          className="xl:col-span-4 xl:min-h-[510px]"
        />
        <FeatureTile
          overline="Evals"
          title="Evals on your own data"
          description={{
            mobile:
              "Score answers with rules or budgeted LLM judges, compare prompt versions and fail the pull request when quality drops.",
            desktop:
              "Build datasets from traces, score answers with rules, LLM judges on a budget or your own endpoint, review them in a queue, compare prompt versions and fail the pull request when quality drops.",
          }}
          illustration={<LandingShot shot="evals" className={SHOT} />}
          illustrationIsContent
          className="xl:col-span-5 xl:min-h-[510px]"
        />
        <FeatureTile
          overline="Alerts & budgets"
          title="Alerts and budgets"
          description={{
            mobile:
              "Slack, email, webhook or PagerDuty when errors, latency or spend cross a line.",
            desktop:
              "Get told on Slack, email, webhook or PagerDuty when errors, latency or spend cross a line, and block calls when a budget runs out.",
          }}
          illustration={<LandingShot shot="alerts" className={SHOT} />}
          illustrationIsContent
          className="xl:col-span-3 xl:min-h-[510px]"
        />
      </div>
    </section>
  );
}
