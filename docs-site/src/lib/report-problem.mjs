/** A GitHub "new issue" link prefilled with the docs page it was opened from. */
export function reportProblemUrl({ repoUrl, title, pageUrl }) {
  const params = new URLSearchParams({
    title: `Docs: ${title}`,
    body: `Page: ${pageUrl}\n\nWhat is wrong or missing?\n`,
  });
  return `${repoUrl}/issues/new?${params}`;
}

/**
 * The page's address for the issue body: the full URL when the public origin is configured
 * (DOCS_SITE_URL), otherwise just the path, so a build never bakes in a local dev address.
 */
export function pageAddress(pathname, site) {
  return site ? new URL(pathname, site).href : pathname;
}
