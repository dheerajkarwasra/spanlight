/** The documentation site, which the web image serves under /docs/ on this same origin. */
const DOCS_BASE = "/docs/";

/**
 * A page of the documentation site, e.g. "gateway/quickstart/" (Starlight paths end in "/"),
 * optionally with an anchor ("concepts/cost/#price-overrides"). Same-origin, so a self-hosted
 * instance links to its own copy of the docs.
 */
export function docsLink(path = ""): string {
  return `${DOCS_BASE}${path.replace(/^\/+/, "")}`;
}
