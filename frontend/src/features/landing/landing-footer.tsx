import { Link } from "@tanstack/react-router";

import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";

import { DOCS_LINKS, EXTERNAL_LINKS, PYPI_URL, SECTION_IDS } from "./links";

const LINK_CLASS =
  "rounded-sm text-sm whitespace-nowrap text-muted-foreground transition-colors hover:text-foreground";

const BOTTOM_LINK_CLASS =
  "rounded-sm underline-offset-2 transition-colors hover:text-foreground hover:underline";

/** A footer entry: a plain link, an in-app route, or the "start the demo" action. */
type FooterItem =
  | { label: string; href: string }
  | { label: string; to: "/login" | "/signup" }
  | { label: string; action: "demo" };

interface FooterColumnData {
  heading: string;
  items: readonly FooterItem[];
}

const COLUMNS: readonly FooterColumnData[] = [
  {
    heading: "Product",
    items: [
      { label: "Cost", href: `#${SECTION_IDS.cost}` },
      { label: "Features", href: `#${SECTION_IDS.features}` },
      { label: "Diagnose", href: `#${SECTION_IDS.diagnose}` },
      { label: "Live demo", action: "demo" },
      { label: "Sign in", to: "/login" },
      { label: "Get started", to: "/signup" },
    ],
  },
  {
    heading: "Developers",
    items: [
      { label: "Quickstart", href: DOCS_LINKS.quickstart },
      { label: "Python SDK", href: DOCS_LINKS.pythonSdk },
      { label: "OpenTelemetry", href: DOCS_LINKS.otlp },
      { label: "API reference", href: DOCS_LINKS.apiReference },
      { label: "Eval gate (CI)", href: DOCS_LINKS.evalGate },
      { label: "Self-hosting", href: DOCS_LINKS.selfHosting },
    ],
  },
  {
    heading: "Guides",
    items: [
      { label: "Gateway", href: DOCS_LINKS.gateway },
      { label: "The Doctor", href: DOCS_LINKS.doctor },
      { label: "Cost and pricing", href: DOCS_LINKS.cost },
      { label: "Attribution", href: DOCS_LINKS.attribution },
      { label: "Evaluators", href: DOCS_LINKS.evaluators },
      { label: "Experiments", href: DOCS_LINKS.experiments },
    ],
  },
  {
    heading: "Project",
    items: [
      { label: "GitHub", href: EXTERNAL_LINKS.github },
      { label: "Changelog", href: DOCS_LINKS.changelog },
      { label: "Contributing", href: EXTERNAL_LINKS.contributing },
      { label: "Security", href: DOCS_LINKS.securityPolicy },
      { label: "License (MIT)", href: EXTERNAL_LINKS.license },
      { label: "PyPI package", href: PYPI_URL },
    ],
  },
];

interface LandingFooterProps {
  onTryDemo: () => void;
  demoPending: boolean;
}

/** Footer card with link columns and the oversized wordmark (Figma "Section/Footer", v3). */
export function LandingFooter({ onTryDemo, demoPending }: LandingFooterProps) {
  const year = new Date().getFullYear();

  return (
    <footer className="px-3 md:px-8">
      <div className="mx-auto flex max-w-[1200px] flex-col gap-8 overflow-hidden rounded-t-card border-x border-t border-border bg-surface px-6 pt-9 md:px-14 md:pt-14">
        <div className="flex flex-col gap-8 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex flex-col items-start gap-4 lg:w-[340px] lg:gap-[18px]">
            <Logo />
            <p className="text-sm text-muted-foreground">
              Open-source LLM observability: traces, auditable cost, diagnoses, evals and a gateway
              on one Postgres.
            </p>
            <Button asChild>
              <a href={EXTERNAL_LINKS.github}>Star on GitHub</a>
            </Button>
          </div>

          <nav aria-label="Footer" className="flex flex-wrap gap-x-6 gap-y-7">
            {COLUMNS.map((column) => (
              <div
                key={column.heading}
                className="flex w-[136px] flex-col gap-3.5 md:w-[140px] xl:w-[160px]"
              >
                <h2 className="text-sm font-semibold text-foreground">{column.heading}</h2>
                <ul className="flex flex-col items-start gap-3.5">
                  {column.items.map((item) => (
                    <li key={item.label}>
                      <FooterLink item={item} onTryDemo={onTryDemo} demoPending={demoPending} />
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
        </div>

        <div className="h-px bg-border" />

        <div className="flex flex-col gap-1.5 text-xs leading-[1.4] font-medium text-subtle-foreground md:flex-row md:justify-between md:text-label md:leading-[1.4]">
          <p>© {year} Spanlight · MIT core</p>
          <p className="flex items-center gap-2">
            <a href={EXTERNAL_LINKS.issues} className={BOTTOM_LINK_CLASS}>
              Report an issue
            </a>
            <span aria-hidden>·</span>
            <a href={DOCS_LINKS.securityPolicy} className={BOTTOM_LINK_CLASS}>
              Security policy
            </a>
          </p>
        </div>

        {/*
         * Decorative wordmark, cropped by the card. Drawn as generated content so it isn't
         * read out or measured as body text (it is deliberately low-contrast).
         */}
        <div
          aria-hidden
          data-wordmark="spanlight"
          className="h-6 overflow-hidden text-[37px] leading-[1.04] font-extrabold tracking-[-0.04em] whitespace-nowrap text-surface-hover before:content-[attr(data-wordmark)] md:h-[83px] md:text-[129px]"
        />
      </div>
    </footer>
  );
}

function FooterLink({ item, onTryDemo, demoPending }: { item: FooterItem } & LandingFooterProps) {
  if ("action" in item) {
    return (
      <button
        type="button"
        onClick={onTryDemo}
        disabled={demoPending}
        aria-busy={demoPending || undefined}
        className={`${LINK_CLASS} cursor-pointer text-left disabled:cursor-progress`}
      >
        {item.label}
      </button>
    );
  }
  if ("to" in item) {
    return (
      <Link to={item.to} className={LINK_CLASS}>
        {item.label}
      </Link>
    );
  }
  return (
    <a href={item.href} className={LINK_CLASS}>
      {item.label}
    </a>
  );
}
