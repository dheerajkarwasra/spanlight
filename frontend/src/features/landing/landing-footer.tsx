import { Link } from "@tanstack/react-router";
import { Play } from "lucide-react";

import { LEGAL_PAGES_ENABLED } from "@/features/legal/legal-enabled";

import { LightField, type DemoActions, type LightBand } from "./landing-parts";
import { LandingScope } from "./landing-scope";
import { inViewRef, useLandingMotion } from "./motion";
import { DOCS_LINKS, EXTERNAL_LINKS, LEGAL_PATHS, PYPI_URL, SECTION_IDS } from "./links";

const LINK_CLASS =
  "lp-control inline-flex min-h-11 items-center rounded-sm text-[15px] leading-[1.4] whitespace-nowrap text-mono-white hover:text-mono-400 xl:min-h-0";

const BOTTOM_LINK_CLASS =
  "lp-control inline-flex min-h-11 items-center rounded-sm underline-offset-2 hover:text-mono-white hover:underline xl:min-h-0";

/** A footer entry: a plain link, an in-app route, or the "start the demo" action. */
type FooterItem =
  | { label: string; href: string }
  | { label: string; to: "/login" | "/signup" | (typeof LEGAL_PATHS)[keyof typeof LEGAL_PATHS] }
  | { label: string; action: "demo" };

interface FooterColumnData {
  heading: string;
  items: readonly FooterItem[];
}

const COLUMNS: readonly FooterColumnData[] = [
  {
    heading: "Product",
    items: [
      { label: "The Doctor", href: `/#${SECTION_IDS.diagnose}` },
      { label: "Gateway", href: `/#${SECTION_IDS.route}` },
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
      { label: "Prompts", href: DOCS_LINKS.prompts },
      { label: "Evaluators", href: DOCS_LINKS.evaluators },
      { label: "Experiments", href: DOCS_LINKS.experiments },
      { label: "Alerts and budgets", href: DOCS_LINKS.alerts },
    ],
  },
  {
    heading: "Project",
    items: [
      { label: "GitHub", href: EXTERNAL_LINKS.github },
      { label: "Changelog", href: DOCS_LINKS.changelog },
      { label: "Contributing", href: EXTERNAL_LINKS.contributing },
      { label: "Security", href: DOCS_LINKS.securityPolicy },
      ...(LEGAL_PAGES_ENABLED
        ? [
            { label: "DPA", to: LEGAL_PATHS.dpa },
            { label: "Subprocessors", to: LEGAL_PATHS.subprocessors },
          ]
        : []),
      { label: "License (MIT)", href: EXTERNAL_LINKS.license },
      { label: "PyPI package", href: PYPI_URL },
    ],
  },
];

/** Bottom row: in-app legal routes first, then the outbound links. */
const BOTTOM_LINKS: readonly Exclude<FooterItem, { action: "demo" }>[] = [
  ...(LEGAL_PAGES_ENABLED
    ? [
        { label: "Privacy", to: LEGAL_PATHS.privacy },
        { label: "Terms", to: LEGAL_PATHS.terms },
        { label: "Imprint", to: LEGAL_PATHS.imprint },
        { label: "Accessibility", to: LEGAL_PATHS.accessibility },
      ]
    : []),
  { label: "Security policy", href: DOCS_LINKS.securityPolicy },
  { label: "Report an issue", href: EXTERNAL_LINKS.issues },
];

/** The lower band of Figma "Close › Light field", behind the wordmark. */
const FOOTER_BANDS: readonly LightBand[] = [
  {
    left: -300,
    top: 40,
    fromBottom: true,
    width: 700,
    height: 220,
    opacity: 0.15,
    rotate: 0,
    blur: 45,
    travel: 200,
    seconds: 8,
  },
];

/**
 * The black footer (Figma "Close"): product, developer, guide and project links, the legal row
 * with the "Pause motion" button, and the clipped wordmark. It carries the landing colour scope
 * itself, so the legal pages get the same footer.
 */
export function LandingFooter({ onTryDemo, demoPending }: DemoActions) {
  const year = new Date().getFullYear();

  return (
    <LandingScope
      as="footer"
      ref={inViewRef}
      className="lp-dark relative overflow-clip bg-mono-black px-5 xl:px-6"
    >
      <LightField bands={FOOTER_BANDS} />
      <div className="relative mx-auto flex max-w-[640px] flex-col gap-10 xl:max-w-[1200px] xl:gap-24">
        <nav
          aria-label="Footer"
          className="grid grid-cols-2 gap-x-6 gap-y-10 border-t border-mono-800 pt-10 xl:grid-cols-4 xl:gap-12 xl:pt-12"
        >
          {COLUMNS.map((column) => (
            <div key={column.heading} className="flex flex-col gap-2 xl:gap-4">
              <h2 className="font-mono text-xs leading-[1.4] font-normal text-mono-400">
                {column.heading}
              </h2>
              <ul className="flex flex-col items-start xl:gap-3">
                {column.items.map((item) => (
                  <li key={item.label}>
                    <FooterLink item={item} onTryDemo={onTryDemo} demoPending={demoPending} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <div className="flex flex-col gap-1 text-[13px] leading-[1.4] text-mono-400 xl:flex-row xl:items-center xl:justify-between">
          <p className="order-2 xl:order-none">© {year} Spanlight</p>
          <ul className="order-1 flex flex-wrap gap-x-5 xl:order-none xl:gap-x-6">
            {BOTTOM_LINKS.map((item) => (
              <li key={item.label}>
                {"to" in item ? (
                  <Link to={item.to} className={BOTTOM_LINK_CLASS}>
                    {item.label}
                  </Link>
                ) : (
                  <a href={item.href} className={BOTTOM_LINK_CLASS}>
                    {item.label}
                  </a>
                )}
              </li>
            ))}
          </ul>
          <div className="order-3 xl:order-none">
            <MotionToggle />
          </div>
        </div>

        {/* Decorative: sized from the column width (299px at 1200), clipped at about 65%. */}
        <div ref={inViewRef} aria-hidden className="lp-view @container">
          <div className="relative h-[16.33cqw] overflow-clip">
            <p className="lp-rise lp-enter-range absolute top-[-4.83cqw] left-0 text-[24.9cqw] leading-none font-semibold tracking-[-0.06em] whitespace-nowrap text-mono-800 [--lp-rise:40px]">
              spanlight
            </p>
          </div>
        </div>
      </div>
    </LandingScope>
  );
}

/** WCAG 2.2.2: one button stops every looping motion on the page, and starts it again. */
function MotionToggle() {
  const motion = useLandingMotion();
  return (
    <button
      type="button"
      aria-pressed={motion.paused}
      onClick={motion.toggle}
      className="lp-control inline-flex min-h-11 items-center gap-1.5 rounded-sm text-[13px] font-medium text-mono-400 hover:text-mono-white"
    >
      {motion.paused ? (
        <Play aria-hidden className="size-2.5 fill-current" />
      ) : (
        <span aria-hidden className="flex gap-1">
          <span className="h-2.5 w-[3px] bg-current" />
          <span className="h-2.5 w-[3px] bg-current" />
        </span>
      )}
      {motion.paused ? "Play motion" : "Pause motion"}
    </button>
  );
}

function FooterLink({ item, onTryDemo, demoPending }: { item: FooterItem } & DemoActions) {
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
