import { Fragment } from "react";

import { CopyButton } from "@/components/copy-button";
import { Button } from "@/components/ui/button";

import { DOCS_LINKS, EXTERNAL_LINKS, SECTION_IDS } from "./links";
import { MeshBlob } from "./mesh-blob";
import { ResponsiveCopy } from "./responsive-copy";

const QUICKSTART_COMMAND = "docker compose up";

const SERVICES = [
  { name: "postgres", status: "Healthy" },
  { name: "migrate", status: "Exited (0)" },
  { name: "api", status: "Started" },
  { name: "worker", status: "Started" },
  { name: "web", status: "Started" },
] as const;

const FACTS = ["MIT licensed", "Postgres 17", "5 services", "eval-gate Action"] as const;

/** The dark open-source band: self-hosting pitch, copyable command and a terminal mock-up. */
export function OpenSourceSection() {
  return (
    <section
      id={SECTION_IDS.openSource}
      aria-labelledby="open-source-title"
      className="scroll-mt-6 px-5 pt-24 md:px-8 lg:pt-40"
    >
      <div className="relative mx-auto flex max-w-[1200px] flex-col gap-7 overflow-hidden rounded-card bg-hero-card px-[22px] pt-9 pb-6 text-hero-card-foreground md:p-12 xl:flex-row xl:items-center xl:gap-14 xl:py-[72px] xl:pr-16 xl:pl-[72px] dark:border dark:border-border">
        <MeshBlob
          tone="violet"
          opacity={0.55}
          className="top-[-120px] left-[160px] h-[260px] w-[300px] blur-[50px] xl:top-[-220px] xl:left-[780px] xl:h-[420px] xl:w-[560px] xl:blur-[85px]"
        />
        <MeshBlob
          tone="fuchsia"
          opacity={0.35}
          className="top-[520px] left-[220px] h-[200px] w-[220px] blur-[50px] xl:top-[260px] xl:left-[1000px] xl:h-[300px] xl:w-[380px] xl:blur-[80px]"
        />
        <MeshBlob
          tone="lime"
          opacity={0.14}
          className="hidden xl:top-[420px] xl:left-[-120px] xl:block xl:h-[220px] xl:w-[300px] xl:blur-[80px]"
        />

        <div className="relative flex flex-col items-start gap-[18px] md:gap-6 xl:w-[540px] xl:shrink-0">
          <p className="text-overline text-lime uppercase">Open source · MIT license</p>
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
            <ResponsiveCopy
              copy={{
                mobile:
                  "MIT licensed. API, worker and dashboard run on one Postgres as one Docker Compose stack, and the eval-gate Action fails a pull request when quality drops.",
                desktop:
                  "MIT licensed. The API, worker and dashboard run on one Postgres as a single Docker Compose stack, and the eval-gate GitHub Action fails a pull request when quality drops. Your prompts and traces stay on infrastructure you control.",
              }}
            />
          </p>

          <div className="flex items-center gap-3 rounded-full border border-rail-tile bg-rail-tile py-1.5 pr-1.5 pl-4 lg:gap-4 lg:py-2 lg:pr-2 lg:pl-5">
            <code className="font-mono text-label leading-[1.5] text-rail-foreground lg:text-code">
              <span className="text-lime">$</span> {QUICKSTART_COMMAND}
            </code>
            <CopyButton
              value={QUICKSTART_COMMAND}
              label="Copy command"
              tone="ink"
              className="size-8 lg:size-9 [&_svg]:size-3.5 lg:[&_svg]:size-4"
            />
          </div>

          <div className="flex w-full flex-col gap-2.5 pt-1 md:w-auto md:flex-row md:gap-3 md:pt-2">
            <Button asChild variant="highlight" size="lg">
              <a href={EXTERNAL_LINKS.github}>Star on GitHub</a>
            </Button>
            <Button asChild size="lg">
              <a href={DOCS_LINKS.selfHosting}>Read the docs</a>
            </Button>
          </div>
        </div>

        <Terminal />
      </div>
    </section>
  );
}

/** What `docker compose up` prints, as a static illustration. */
function Terminal() {
  return (
    <figure className="relative flex min-w-0 flex-1 flex-col gap-3 rounded-tile border border-rail-tile bg-rail-tile px-4 pt-3.5 pb-4 lg:gap-4 lg:px-[22px] lg:pt-[18px] lg:pb-[22px]">
      <figcaption className="flex items-center justify-between">
        <span aria-hidden className="flex gap-[5px] lg:gap-1.5">
          <span className="size-2 rounded-full bg-rail-tile lg:size-[9px]" />
          <span className="size-2 rounded-full bg-rail-tile lg:size-[9px]" />
          <span className="size-2 rounded-full bg-rail-tile lg:size-[9px]" />
        </span>
        {/* Brighter than the Figma label: the violet glow behind it would fail AA otherwise. */}
        <span className="font-mono text-2xs leading-[1.5] text-rail-foreground lg:text-xs">
          ~/spanlight
        </span>
      </figcaption>
      <pre className="overflow-x-auto font-mono text-xs leading-[1.5] text-rail-muted-foreground lg:text-label">
        <span className="text-lime">$</span>{" "}
        <span className="text-rail-foreground">
          <ResponsiveCopy
            copy={{
              mobile: "docker compose up -d",
              desktop: "docker compose -f deploy/compose.yaml up -d",
            }}
          />
        </span>
        {"\n"}
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
