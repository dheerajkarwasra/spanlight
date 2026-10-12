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

/** The documentation site, which the web image serves under /docs/ on this same origin. */
const DOCS_BASE = "/docs/";

/** A page of the documentation site, e.g. "gateway/quickstart/" (Starlight paths end in "/"). */
export function docsLink(path = ""): string {
  return `${DOCS_BASE}${path.replace(/^\/+/, "")}`;
}

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
  evaluators: docsLink("evaluators/"),
  experiments: docsLink("experiments/"),
  alerts: docsLink("alerts/"),
  changelog: docsLink("changelog/"),
  security: docsLink("security/"),
  securityPolicy: docsLink("security/policy/"),
} as const;

/** Ids of the landing sections that the nav and footer scroll to. */
export const SECTION_IDS = {
  features: "features",
  beyondTracing: "beyond-tracing",
  howItWorks: "how-it-works",
  openSource: "open-source",
} as const;

export interface NavLink {
  label: string;
  href: string;
}

/** Header navigation (desktop bar and mobile sheet). */
export const NAV_LINKS: readonly NavLink[] = [
  { label: "Features", href: `#${SECTION_IDS.features}` },
  { label: "Beyond tracing", href: `#${SECTION_IDS.beyondTracing}` },
  { label: "How it works", href: `#${SECTION_IDS.howItWorks}` },
  { label: "Open source", href: `#${SECTION_IDS.openSource}` },
  { label: "Docs", href: DOCS_LINKS.quickstart },
];
