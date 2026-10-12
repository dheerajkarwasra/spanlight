import { FeatureTile } from "./feature-tile";
import { CostIllustration } from "./illustrations/cost-illustration";
import { OtelIllustration } from "./illustrations/otel-illustration";
import { PrivacyIllustration } from "./illustrations/privacy-illustration";
import { SdkIllustration } from "./illustrations/sdk-illustration";
import { SessionsIllustration } from "./illustrations/sessions-illustration";
import { WaterfallIllustration } from "./illustrations/waterfall-illustration";
import { SECTION_IDS } from "./links";
import { SectionHeading, TitleAccent } from "./section-heading";

/**
 * Feature bento (Figma "Section/Features"): one column on phones, two on tablets and the
 * 12-column grid (7+5, 4+5+3, 12) from 1280px.
 */
export function FeaturesSection() {
  return (
    <section
      id={SECTION_IDS.features}
      aria-labelledby="features-title"
      className="scroll-mt-6 px-5 pt-24 md:px-8 lg:pt-40"
    >
      <SectionHeading
        id="features-title"
        overline="Features"
        title={
          <>
            Everything you need to ship LLM features <TitleAccent>with confidence</TitleAccent>
          </>
        }
        description="Debug, measure and cost every model call, from the first prompt in development to real traffic in production."
      />

      <div className="mx-auto mt-9 grid max-w-[1200px] grid-cols-1 gap-4 md:grid-cols-2 lg:mt-14 xl:grid-cols-12 xl:gap-5">
        <FeatureTile
          tone="ink"
          overline="Traces"
          title="Trace explorer"
          description={{
            mobile:
              "Follow one request through LLM calls, tools and retrieval on a single waterfall, with time to first token for every span.",
            desktop:
              "Follow one request through LLM calls, tools and retrieval on a single waterfall, with prompts, completions, latency and time to first token for every span.",
          }}
          illustration={<WaterfallIllustration />}
          className="md:col-span-2 xl:col-span-7 xl:min-h-[507px]"
        />
        <FeatureTile
          overline="Cost"
          title="Cost you can check"
          description={{
            mobile:
              "Per-call cost from a versioned price table. Unknown prices show as —, never $0.",
            desktop:
              "Per-call cost from a versioned price table, rolled up by trace, session and model. Unknown prices show as —, never $0.",
          }}
          illustration={<CostIllustration />}
          className="xl:col-span-5 xl:min-h-[507px]"
        />
        <FeatureTile
          overline="Sessions"
          title="Sessions"
          description="Group multi-turn conversations and replay them as chat, with cost and latency for every turn."
          illustration={<SessionsIllustration />}
          className="xl:col-span-4 xl:min-h-[510px]"
        />
        <FeatureTile
          overline="Python SDK"
          title="Drop-in Python SDK"
          description={{
            mobile:
              "pip install spanlight, wrap your client, fetch versioned prompts and send scores. SDK failures never reach your code.",
            desktop:
              "pip install spanlight and wrap your OpenAI or Anthropic client. Fetch versioned prompts, send scores with spanlight.score(), and let spans batch in the background; SDK failures never reach your code.",
          }}
          illustration={<SdkIllustration />}
          className="xl:col-span-5 xl:min-h-[510px]"
        />
        <FeatureTile
          overline="OpenTelemetry"
          title="OpenTelemetry native"
          description="Send OTLP/HTTP from any language. gen_ai.* attributes become prompts, models, tokens and cost."
          illustration={<OtelIllustration />}
          className="xl:col-span-3 xl:min-h-[510px]"
        />
        <FeatureTile
          layout="split"
          overline="Privacy"
          title="Private by design"
          description={{
            mobile:
              "Trace data is isolated by Postgres row-level security. Secrets are redacted on ingest; payload capture can be switched off.",
            desktop:
              "Trace data is isolated by Postgres row-level security. Secrets are redacted on ingest, and payload capture can be switched off per project.",
          }}
          illustration={<PrivacyIllustration />}
          className="md:col-span-2 xl:col-span-12 xl:min-h-[261px]"
        />
      </div>
    </section>
  );
}
