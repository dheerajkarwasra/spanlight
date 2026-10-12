import { CodeSampleCard } from "./code-sample-card";
import { SECTION_IDS } from "./links";
import { ResponsiveCopy, type Copy } from "./responsive-copy";
import { SectionHeading } from "./section-heading";

interface Step {
  title: string;
  description: Copy;
  /** Mono chip under the description. */
  detail: Copy;
}

const STEPS: readonly Step[] = [
  {
    title: "Self-host or try the demo",
    description:
      "Run docker compose up on your own machine, or explore the live demo workspace first.",
    detail: "docker compose up -d",
  },
  {
    title: "Add three lines, or one base URL",
    description: {
      mobile:
        "Wrap your OpenAI or Anthropic client, send OTLP from any language, or point your client at the gateway.",
      desktop:
        "Wrap your OpenAI or Anthropic client, point an OpenTelemetry exporter at /v1/otlp/traces, or set your client’s base URL to the gateway.",
    },
    detail: 'pip install "spanlight[anthropic]"',
  },
  {
    title: "Watch traces arrive live",
    description: {
      mobile:
        "Each call appears with its prompt, completion, latency, tokens and cost seconds after it runs.",
      desktop:
        "Each call appears with its prompt, completion, latency, tokens and cost a few seconds after it runs.",
    },
    detail: {
      mobile: "2 spans · 1.42 s · $0.0031",
      desktop: "answer_ticket · 2 spans · 1.42 s · $0.0031",
    },
  },
];

/** Three numbered steps beside the integration code card (Figma "Section/How it works"). */
export function HowItWorksSection() {
  return (
    <section
      id={SECTION_IDS.howItWorks}
      aria-labelledby="how-it-works-title"
      className="scroll-mt-6 px-5 pt-24 md:px-8 lg:pt-40"
    >
      <SectionHeading
        id="how-it-works-title"
        overline="How it works"
        title="From zero to traces in three steps"
        description="Send traces with the SDK, any OpenTelemetry exporter or the gateway. Only the gateway sits in the request path, and only if you choose it."
      />

      <div className="mx-auto mt-9 grid max-w-[1200px] grid-cols-1 gap-9 lg:mt-14 xl:grid-cols-12 xl:gap-5">
        <ol className="flex flex-col gap-3 md:gap-4 xl:col-span-5">
          {STEPS.map((step, index) => (
            <StepCard key={step.title} number={index + 1} step={step} />
          ))}
        </ol>
        <CodeSampleCard className="xl:col-span-7" />
      </div>
    </section>
  );
}

/** Figma "Landing/Step card": ink number disc, title, description and a mono detail chip. */
function StepCard({ number, step }: { number: number; step: Step }) {
  return (
    <li className="flex flex-1 items-start gap-3.5 rounded-card border border-border bg-surface p-5 shadow-card md:gap-[18px] md:p-6">
      <span
        aria-hidden
        className="flex size-11 shrink-0 items-center justify-center rounded-full bg-hero-card text-base leading-[1.4] font-extrabold tracking-[-0.01em] text-lime dark:border dark:border-border"
      >
        {number}
      </span>
      <div className="flex min-w-0 flex-1 flex-col items-start gap-2 pt-0.5">
        <h3 className="text-h2 text-foreground">{step.title}</h3>
        <p className="text-sm text-muted-foreground">
          <ResponsiveCopy copy={step.description} />
        </p>
        <span className="max-w-full truncate rounded-full bg-surface-muted px-3 py-1.5 font-mono text-xs leading-[1.5] text-muted-foreground">
          <ResponsiveCopy copy={step.detail} />
        </span>
      </div>
    </li>
  );
}
