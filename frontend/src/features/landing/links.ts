import { docsLink } from "@/lib/docs-links";

/**
 * Every outbound and in-page link on the landing page, in one place.
 *
 * REPOSITORY_URL is the public repository. Every GitHub link below is derived from it, so moving
 * the repository means changing this one line. Documentation links point at the docs site on
 * this same origin, so a self-hosted instance links to its own copy of the docs.
 */
export const REPOSITORY_URL = "https://github.com/dheerajkarwasra/spanlight";

/** Branch that file links open on. */
const DEFAULT_BRANCH = "main";

/**
 * A link into the repository: the repository itself (no target), a README anchor ("#readme")
 * or a file path, optionally with an anchor ("docs/api-deviations.md#errors").
 */
export function repositoryLink(repositoryUrl: string, target?: string): string {
  const base = repositoryUrl.replace(/\/+$/, "");
  if (!target) {
    return base;
  }
  if (target.startsWith("#")) {
    return `${base}${target}`;
  }
  return `${base}/blob/${DEFAULT_BRANCH}/${target.replace(/^\/+/, "")}`;
}

export const EXTERNAL_LINKS = {
  github: repositoryLink(REPOSITORY_URL),
  issues: `${repositoryLink(REPOSITORY_URL)}/issues`,
  license: repositoryLink(REPOSITORY_URL, "LICENSE"),
  contributing: repositoryLink(REPOSITORY_URL, "CONTRIBUTING.md"),
} as const;

/** The Python SDK on PyPI. */
export const PYPI_URL = "https://pypi.org/project/spanlight/";

export const DOCS_LINKS = {
  home: docsLink(),
  quickstart: docsLink("quickstart/"),
  pythonSdk: docsLink("python-sdk/"),
  otlp: docsLink("otlp/"),
  apiReference: docsLink("api/"),
  evalGate: docsLink("eval-gate/"),
  selfHosting: docsLink("self-hosting/"),
  gateway: docsLink("gateway/quickstart/"),
  doctor: docsLink("doctor/"),
  prompts: docsLink("prompts/"),
  alerts: docsLink("alerts/"),
  evaluators: docsLink("evaluators/"),
  experiments: docsLink("experiments/"),
  cost: docsLink("concepts/cost/"),
  attribution: docsLink("concepts/attribution/"),
  changelog: docsLink("changelog/"),
  securityPolicy: docsLink("security/policy/"),
} as const;

/** Ids of the landing sections that the nav and footer scroll to. */
export const SECTION_IDS = {
  cost: "cost",
  features: "features",
  diagnose: "diagnose",
  howItWorks: "how-it-works",
  openSource: "open-source",
} as const;

export interface NavLink {
  label: string;
  href: string;
}

/** Header navigation (desktop bar and mobile sheet). */
export const NAV_LINKS: readonly NavLink[] = [
  { label: "Product", href: `#${SECTION_IDS.cost}` },
  { label: "How it works", href: `#${SECTION_IDS.howItWorks}` },
  { label: "Self-hosting", href: `#${SECTION_IDS.openSource}` },
  { label: "Docs", href: DOCS_LINKS.quickstart },
];
