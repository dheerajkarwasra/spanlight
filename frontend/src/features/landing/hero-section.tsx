import { ArrowRight, Check } from "lucide-react";

import { LogoMark } from "@/components/logo";
import { Button } from "@/components/ui/button";

import { DOCS_LINKS, SECTION_IDS } from "./links";
import { TraceAnatomy } from "./trace-anatomy";

const HIGHLIGHTS = ["MIT-licensed core", "No credit card", "Self-host with Docker"] as const;

interface HeroSectionProps {
  onTryDemo: () => void;
  demoPending: boolean;
}

/** Headline, value proposition, the two primary actions and the trace-and-cost figure. */
export function HeroSection({ onTryDemo, demoPending }: HeroSectionProps) {
  return (
    <section aria-labelledby="hero-title" className="relative px-5 pt-8 md:px-8 lg:pt-16">
      <div className="mx-auto flex max-w-[778px] flex-col items-center gap-[22px] text-center lg:gap-7">
        <a
          href={`#${SECTION_IDS.openSource}`}
          className="flex animate-fade-up items-center gap-2 rounded-full border border-border bg-surface py-[5px] pr-3.5 pl-[5px] text-label font-semibold text-foreground transition-colors hover:bg-surface-muted lg:gap-2.5 lg:py-1.5 lg:pr-4 lg:pl-1.5 lg:text-sm"
        >
          <span
            aria-hidden
            className="flex size-[22px] items-center justify-center rounded-full bg-hero-card lg:size-[26px] dark:border dark:border-border"
          >
            <LogoMark tone="on-dark" className="size-3 lg:size-4" />
          </span>
          Open-source LLM observability
          <ArrowRight aria-hidden className="hidden size-3.5 text-subtle-foreground lg:block" />
        </a>

        <h1
          id="hero-title"
          className="flex animate-fade-up flex-col items-center font-extrabold text-foreground [animation-delay:60ms]"
        >
          <span className="text-[42px] leading-[1.04] tracking-[-0.04em] md:text-[64px] lg:text-[88px]">
            See every{" "}
            <span className="mx-auto mt-1 block w-fit pb-0.5 md:mt-0 md:inline-block lg:pb-1">
              LLM call
            </span>
          </span>{" "}
          <span className="mt-1 font-serif-accent text-[58px] leading-none text-accent md:mt-0 md:text-[84px] lg:-mt-1 lg:text-[112px]">
            clearly.
          </span>
        </h1>

        <p className="max-w-[340px] animate-fade-up text-lg text-muted-foreground [animation-delay:120ms] md:max-w-[760px] lg:text-[19px] lg:leading-[1.55]">
          Trace LLM requests, inspect costs line by line, and investigate failures with the Doctor.
          Open source. Self-hosted on one Postgres.
        </p>

        <div className="flex w-full animate-fade-up flex-col items-center gap-[22px] pt-1.5 [animation-delay:180ms] lg:gap-5 lg:pt-3">
          <div className="flex w-full flex-col gap-2.5 md:w-auto md:flex-row md:gap-3">
            <Button variant="primary" size="lg" loading={demoPending} onClick={onTryDemo}>
              Try the live demo
            </Button>
            <Button asChild size="lg" className="shadow-none">
              <a href={DOCS_LINKS.selfHosting}>Self-host Spanlight</a>
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

      <TraceAnatomy className="mt-8 animate-fade-up [animation-delay:240ms] lg:mt-10" />
    </section>
  );
}
