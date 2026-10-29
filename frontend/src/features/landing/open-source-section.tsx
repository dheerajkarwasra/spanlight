import { Fragment } from "react";

import { Button } from "@/components/ui/button";

import { DOCS_LINKS, EXTERNAL_LINKS, SECTION_IDS } from "./links";

const SERVICES = [
  { name: "postgres", status: "Healthy" },
  { name: "migrate", status: "Exited (0)" },
  { name: "api", status: "Started" },
  { name: "worker", status: "Started" },
  { name: "web", status: "Started" },
] as const;

const FACTS = ["MIT core", "Postgres 17", "5 services", "eval-gate Action"] as const;

/** The dark open-source band: ownership, privacy and a guided self-hosting action. */
export function OpenSourceSection() {
  return (
    <section
      id={SECTION_IDS.openSource}
      aria-labelledby="open-source-title"
      className="scroll-mt-6 px-5 pt-16 md:px-8 lg:pt-24"
    >
      <div className="relative mx-auto flex max-w-[1200px] flex-col gap-7 overflow-hidden rounded-card bg-hero-card px-[22px] pt-9 pb-6 text-hero-card-foreground md:p-12 xl:flex-row xl:items-center xl:gap-14 xl:py-[72px] xl:pr-16 xl:pl-[72px] dark:border dark:border-border">
        <div className="relative flex flex-col items-start gap-[18px] md:gap-6 xl:w-[540px] xl:shrink-0">
          <p className="text-overline text-lime uppercase">Open source · MIT core</p>
          <h2
            id="open-source-title"
            className="text-[36px] leading-[1.04] font-extrabold tracking-[-0.04em] text-rail-foreground md:text-display"
          >
            Open source.
            <br />
            Self-hosted. <br className="md:hidden" />
            <span className="text-lime">Yours.</span>
          </h2>
          <p className="text-base leading-[1.55] text-rail-muted-foreground md:text-lg">
            MIT core. One Postgres. Your prompts and traces stay on infrastructure you control, with
            project isolation, secret redaction and optional payload capture.
          </p>

          <div className="flex w-full flex-col gap-2.5 pt-1 md:w-auto md:flex-row md:gap-3 md:pt-2">
            <Button asChild variant="highlight" size="lg">
              <a href={DOCS_LINKS.selfHosting}>Self-hosting guide</a>
            </Button>
            <Button asChild size="lg" className="shadow-none">
              <a href={EXTERNAL_LINKS.github}>View on GitHub</a>
            </Button>
          </div>
        </div>

        <Terminal />
      </div>
    </section>
  );
}

/** Example output after following the clone and configuration steps in the guide. */
function Terminal() {
  return (
    <figure className="relative flex min-w-0 flex-1 flex-col gap-3 rounded-tile border border-rail-tile bg-rail-tile px-4 pt-3.5 pb-4 lg:gap-4 lg:px-[22px] lg:pt-[18px] lg:pb-[22px]">
      <figcaption className="flex items-center justify-between">
        <span aria-hidden className="flex gap-[5px] lg:gap-1.5">
          <span className="size-2 rounded-full bg-rail-tile lg:size-[9px]" />
          <span className="size-2 rounded-full bg-rail-tile lg:size-[9px]" />
          <span className="size-2 rounded-full bg-rail-tile lg:size-[9px]" />
        </span>
        <span className="font-mono text-2xs leading-[1.5] text-rail-foreground lg:text-xs">
          ~/spanlight
        </span>
      </figcaption>
      <pre className="overflow-x-auto font-mono text-xs leading-[1.5] text-rail-muted-foreground lg:text-label">
        <span className="text-rail-muted-foreground"># After clone + configuration</span>
        {"\n"}
        <span className="text-lime">$</span>{" "}
        <span className="text-rail-foreground">
          {"docker compose \\\n  -f deploy/compose.yaml \\\n  up -d"}
        </span>
        {"\n\n"}
        {SERVICES.map((service) => (
          <Fragment key={service.name}>
            <span className="text-lime">✔</span>{" "}
            <span className="text-rail-foreground">{service.name.padEnd(11)}</span>
            {service.status}
            {"\n"}
          </Fragment>
        ))}
        {"\n"}
        <span className="text-lime">→</span> Open{" "}
        <span className="text-rail-foreground">http://localhost:8080</span>
      </pre>
      <ul className="flex flex-wrap gap-1.5 lg:gap-2">
        {FACTS.map((fact) => (
          <li
            key={fact}
            className="rounded-full bg-rail-tile px-2.5 py-1 text-xs font-medium text-rail-foreground lg:px-3 lg:py-[5px]"
          >
            {fact}
          </li>
        ))}
      </ul>
    </figure>
  );
}
