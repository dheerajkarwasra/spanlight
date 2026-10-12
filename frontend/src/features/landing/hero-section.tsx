import { Link } from "@tanstack/react-router";
import { Activity, ArrowRight, Check } from "lucide-react";

import { Button } from "@/components/ui/button";

import { SECTION_IDS } from "./links";
import { ProductPreview } from "./product-preview";

const HIGHLIGHTS = ["Free and open source", "No credit card", "Self-host with Docker"] as const;

interface HeroSectionProps {
  onTryDemo: () => void;
  demoPending: boolean;
}

/** Headline, value proposition, the two primary actions and the product preview. */
export function HeroSection({ onTryDemo, demoPending }: HeroSectionProps) {
  return (
    <section aria-labelledby="hero-title" className="relative px-5 pt-[52px] md:px-8 lg:pt-24">
      <div className="mx-auto flex max-w-[778px] flex-col items-center gap-[22px] text-center lg:gap-7">
        <a
          href={`#${SECTION_IDS.openSource}`}
          className="flex animate-fade-up items-center gap-2 rounded-full border border-border bg-surface py-[5px] pr-3.5 pl-[5px] text-label font-semibold text-foreground shadow-card transition-colors hover:bg-surface-muted lg:gap-2.5 lg:py-1.5 lg:pr-4 lg:pl-1.5 lg:text-sm"
        >
          <span
            aria-hidden
            className="flex size-[22px] items-center justify-center rounded-full bg-lime text-lime-foreground lg:size-[26px]"
          >
            <Activity className="size-3 lg:size-3.5" strokeWidth={2.4} />
          </span>
          Open-source LLM observability
          <ArrowRight aria-hidden className="hidden size-3.5 text-subtle-foreground lg:block" />
        </a>

        <h1
          id="hero-title"
          className="flex animate-fade-up flex-col items-center font-extrabold text-foreground [animation-delay:60ms]"
        >
          <span className="text-[42px] leading-[1.04] tracking-[-0.04em] md:text-[64px] lg:text-[88px]">
            See each{" "}
            <span className="mx-auto mt-1 block w-fit rounded-[16px] bg-lime px-3.5 pb-0.5 text-lime-foreground md:mt-0 md:inline-block md:rounded-[20px] md:px-4 lg:rounded-[26px] lg:px-[22px] lg:pb-1">
              LLM call
            </span>
          </span>{" "}
          <span className="mt-1 font-serif-accent text-[58px] leading-none text-accent md:mt-0 md:text-[84px] lg:-mt-1 lg:text-[112px]">
            clearly.
          </span>
        </h1>

        <p className="max-w-[340px] animate-fade-up text-lg text-muted-foreground [animation-delay:120ms] md:max-w-[700px] lg:text-[19px] lg:leading-[1.55]">
          Trace prompts, completions, latency, tokens and cost across OpenAI, Anthropic and any
          OpenTelemetry app. Self-host on Postgres in minutes.
        </p>

        <div className="flex w-full animate-fade-up flex-col items-center gap-[22px] pt-1.5 [animation-delay:180ms] lg:gap-5 lg:pt-3">
          <div className="flex w-full flex-col gap-2.5 md:w-auto md:flex-row md:gap-3">
            <Button variant="primary" size="lg" loading={demoPending} onClick={onTryDemo}>
              Try the live demo
            </Button>
            <Button asChild size="lg">
              <Link to="/signup">Get started — it’s free</Link>
            </Button>
          </div>
          <ul className="flex flex-wrap items-center justify-center gap-x-3.5 gap-y-2 lg:gap-x-[22px]">
            {HIGHLIGHTS.map((highlight) => (
              <li
                key={highlight}
                className="flex items-center gap-[5px] text-xs font-medium text-muted-foreground lg:gap-1.5 lg:text-label lg:leading-[1.4]"
              >
                <Check
                  aria-hidden
                  className="size-[13px] text-success lg:size-3.5"
                  strokeWidth={2.6}
                />
                {highlight}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <ProductPreview className="mt-11 animate-fade-up [animation-delay:240ms] lg:mt-[72px]" />
    </section>
  );
}
