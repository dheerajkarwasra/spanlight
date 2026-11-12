import { BUTTON_PRIMARY, BUTTON_SECONDARY } from "./button-styles";
import { Figure, Section, SectionHeading, TitleSecondLine, Wires } from "./landing-parts";
import { inViewRef, stagger } from "./motion";
import { DOCS_LINKS, EXTERNAL_LINKS, SECTION_IDS } from "./links";

/** The three roles of the one image, left to right, each wired to the one Postgres. */
const SERVICES = [
  { name: "api", left: 37 },
  { name: "worker", left: 237 },
  { name: "web", left: 437 },
] as const;

/** Wire paths from the Figma vectors, in a 636 × 330 box. */
const SERVICE_WIRES = [
  "M118 96C118 150 318 150 318 204",
  "M318 96V204",
  "M518 96C518 150 318 150 318 204",
] as const;

/** Example output after the clone and configuration steps in the self-hosting guide. */
const TERMINAL_COMMAND = [
  "docker compose -f deploy/compose.yaml \\",
  "    --env-file deploy/.env up -d",
] as const;
const TERMINAL_OUTPUT = [
  "✓ postgres   Healthy",
  "✓ migrate    Exited (0)",
  "✓ api        Started",
  "✓ worker     Started",
  "✓ web        Started",
] as const;

const FACTS = [
  { value: "MIT", text: "core licence" },
  { value: "1", text: "datastore" },
  { value: "5", text: "Compose services" },
  { value: "0.8.1", text: "server release, pre-1.0" },
] as const;

/** "Own": one Postgres, the Compose stack and the licence facts (Figma "Own"). */
export function OwnSection() {
  return (
    <Section id={SECTION_IDS.own} tone="black">
      <SectionHeading
        id={SECTION_IDS.own}
        dark
        label="Own"
        title={<TitleSecondLine first="One Postgres." second="Yours to run." />}
        description="The only datastore, the job queue included. MIT core."
        mobileDescription="Postgres is the only datastore, the job queue included. Your prompts and traces stay on infrastructure you control, isolated per project by row-level security, with secrets redacted before storage."
      />

      <div className="order-2 flex w-full flex-col gap-6 xl:order-none xl:flex-row xl:items-start">
        <Figure
          label="The api, worker and web services all feed one Postgres 17, which holds traces, spans, the job queue, rate limits and prices."
          className="relative hidden h-[330px] w-[636px] shrink-0 overflow-clip rounded-xl border border-mono-800 bg-mono-900 xl:block"
        >
          {/* #3a3a3a is the Figma wire colour here: mono/800 would vanish on the #171717 card. */}
          <Wires
            paths={SERVICE_WIRES}
            width={636}
            height={330}
            stroke="#3a3a3a"
            pulse="var(--mono-white)"
            className="-top-px -left-px"
          />
          {SERVICES.map((service) => (
            <span
              key={service.name}
              style={{ left: service.left }}
              className="absolute top-[35px] flex h-[60px] w-40 items-center justify-center rounded-[10px] border border-mono-800 bg-mono-black text-[15px] leading-[1.3] font-medium tracking-[-0.01em] text-mono-white"
            >
              {service.name}
            </span>
          ))}
          <span
            style={stagger(3)}
            className="lp-rise absolute top-[203px] left-[77px] flex h-[90px] w-[480px] flex-col items-center justify-center gap-[3px] rounded-[10px] bg-mono-white"
          >
            <span className="text-xl leading-[1.3] font-medium tracking-[-0.01em] text-mono-black">
              Postgres 17
            </span>
            <span className="font-mono text-xs leading-[1.4] text-mono-600">
              traces · spans · job queue · rate limits · prices
            </span>
          </span>
        </Figure>

        <div className="flex min-w-0 flex-col gap-3.5 xl:w-[540px]">
          <Figure
            label="Example terminal output: docker compose with deploy/compose.yaml and deploy/.env starts postgres, migrate, api, worker and web."
            className="flex flex-col overflow-hidden rounded-xl border border-mono-800 bg-mono-900"
          >
            <span className="hidden border-b border-mono-800 px-5 py-3.5 font-mono text-xs leading-[1.4] text-mono-400 xl:block">
              ~/spanlight
            </span>
            <pre className="overflow-x-auto px-5 pt-5 pb-6 font-mono text-xs leading-[1.75] text-mono-400 xl:px-6 xl:pt-[22px] xl:text-[13.5px]">
              <span className="block">
                $ <span className="text-mono-white">{TERMINAL_COMMAND[0]}</span>
              </span>
              <span className="block text-mono-white">{TERMINAL_COMMAND[1]}</span>
              <span className="block min-h-[1lh]" />
              {TERMINAL_OUTPUT.map((line, index) => (
                <span key={line} style={stagger(index + 1)} className="lp-fade block">
                  {line}
                </span>
              ))}
            </pre>
          </Figure>
          <p className="hidden text-xs leading-[1.4] text-mono-400 xl:block">
            Example output after the clone and configuration steps in the guide.
          </p>
        </div>
      </div>

      <ul
        ref={inViewRef}
        className="lp-view order-1 grid w-full grid-cols-2 gap-x-4 gap-y-7 xl:order-none xl:grid-cols-4 xl:gap-6"
      >
        {FACTS.map((fact, index) => (
          <li
            key={fact.text}
            style={stagger(index)}
            className="lp-rise flex flex-col gap-1.5 border-t border-mono-800 pt-[18px]"
          >
            <span className="text-[34px] leading-[1.05] font-medium tracking-[-0.035em] text-mono-white xl:text-[44px]">
              {fact.value}
            </span>
            <span className="text-[13px] leading-[1.4] text-mono-400">{fact.text}</span>
          </li>
        ))}
      </ul>

      <div className="order-3 flex w-full flex-col gap-2.5 xl:order-none xl:w-auto xl:flex-row xl:gap-3">
        <a href={DOCS_LINKS.selfHosting} className={BUTTON_PRIMARY}>
          Self-hosting guide
        </a>
        <a href={EXTERNAL_LINKS.github} className={BUTTON_SECONDARY}>
          View on GitHub
        </a>
      </div>
    </Section>
  );
}
