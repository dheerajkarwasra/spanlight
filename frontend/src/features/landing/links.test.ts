import { describe, expect, it } from "vitest";

import {
  DOCS_LINKS,
  EXTERNAL_LINKS,
  NAV_LINKS,
  REPOSITORY_URL,
  docsLink,
  repositoryLink,
} from "./links";

/** Every page of the docs site, keyed by its source path ("…/content/docs/gateway/lab.md"). */
const DOCS_PAGES = Object.keys(
  import.meta.glob("../../../../docs-site/src/content/docs/**/*.{md,mdx}"),
);

/** The docs-site source file a "/docs/…/" link is served from, or undefined if none. */
function docsPageFor(href: string): string | undefined {
  const slug = href.replace(/^\/docs\//, "").replace(/\/$/, "");
  const candidates = slug
    ? [`${slug}.md`, `${slug}.mdx`, `${slug}/index.md`, `${slug}/index.mdx`]
    : ["index.md", "index.mdx"];
  return DOCS_PAGES.find((page) =>
    candidates.some((candidate) => page.endsWith(`/content/docs/${candidate}`)),
  );
}

const REPO = "https://github.com/acme/spanlight";

describe("repositoryLink", () => {
  it("returns the repository itself without a target", () => {
    expect(repositoryLink(REPO)).toBe(REPO);
    expect(repositoryLink(`${REPO}/`)).toBe(REPO);
  });

  it("appends README anchors to the repository URL", () => {
    expect(repositoryLink(REPO, "#readme")).toBe(`${REPO}#readme`);
  });

  it("links files on the default branch, keeping their anchor", () => {
    expect(repositoryLink(REPO, "sdks/python/README.md")).toBe(
      `${REPO}/blob/main/sdks/python/README.md`,
    );
    expect(repositoryLink(`${REPO}/`, "/docs/api-deviations.md#errors")).toBe(
      `${REPO}/blob/main/docs/api-deviations.md#errors`,
    );
  });
});

describe("docsLink", () => {
  it("points at the docs site on the same origin", () => {
    expect(docsLink()).toBe("/docs/");
    expect(docsLink("gateway/quickstart/")).toBe("/docs/gateway/quickstart/");
    expect(docsLink("/doctor/")).toBe("/docs/doctor/");
  });
});

describe("landing links", () => {
  it("point at the real Spanlight repository", () => {
    expect(REPOSITORY_URL).toMatch(/^https:\/\/github\.com\/[^/]+\/spanlight$/);
  });

  it("derive every external link from the repository URL", () => {
    for (const href of Object.values(EXTERNAL_LINKS)) {
      expect(href.startsWith(REPOSITORY_URL.replace(/\/+$/, ""))).toBe(true);
    }
  });

  it("link docs pages as same-origin directory paths", () => {
    for (const href of Object.values(DOCS_LINKS)) {
      expect(href).toMatch(/^\/docs\/([a-z0-9-]+\/)*$/);
    }
  });

  it("scroll to the landing sections from the nav, then open the quickstart", () => {
    expect(NAV_LINKS.map((link) => link.href)).toEqual([
      "#features",
      "#beyond-tracing",
      "#how-it-works",
      "#open-source",
      DOCS_LINKS.quickstart,
    ]);
  });

  it("only link docs pages that exist in the docs site", () => {
    expect(DOCS_PAGES.length).toBeGreaterThan(0);
    const docsHrefs = [
      ...Object.values(DOCS_LINKS),
      ...NAV_LINKS.map((link) => link.href).filter((href) => href.startsWith("/docs/")),
    ];
    const missing = docsHrefs.filter((href) => docsPageFor(href) === undefined);
    expect(missing).toEqual([]);
  });

  it("keep evaluators and experiments as separate guides", () => {
    expect(DOCS_LINKS.evaluators).toBe("/docs/evaluators/");
    expect(DOCS_LINKS.experiments).toBe("/docs/experiments/");
  });
});
