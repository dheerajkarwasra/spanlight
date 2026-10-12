/**
 * Every outbound and in-page link on the landing page, in one place.
 *
 * REPOSITORY_URL is the public repository. Every GitHub and docs link below is derived
 * from it, so moving the repository means changing this one line.
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
  /** "Read the docs" and "Docs": the README for now. */
  docs: repositoryLink(REPOSITORY_URL, "#readme"),
  pythonSdk: repositoryLink(REPOSITORY_URL, "sdks/python/README.md"),
  otlp: repositoryLink(REPOSITORY_URL, "docs/api-deviations.md#otlp-post-v1otlptraces"),
  apiReference: repositoryLink(REPOSITORY_URL, "docs/api-deviations.md"),
  license: repositoryLink(REPOSITORY_URL, "LICENSE"),
  selfHosting: repositoryLink(REPOSITORY_URL, "#1-start-the-platform"),
  security: repositoryLink(REPOSITORY_URL, "docs/decisions/0003-row-level-security.md"),
} as const;

/** Ids of the landing sections that the nav and footer scroll to. */
export const SECTION_IDS = {
  features: "features",
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
  { label: "How it works", href: `#${SECTION_IDS.howItWorks}` },
  { label: "Open source", href: `#${SECTION_IDS.openSource}` },
  { label: "Docs", href: EXTERNAL_LINKS.docs },
];
