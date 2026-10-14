/**
 * The newest release in CHANGELOG.md ("## [x.y.z] - <status>") and the anchor Starlight gives
 * that heading (github-slugger rules: lowercase, punctuation dropped, spaces become hyphens).
 */
export function latestRelease(changelogMarkdown) {
  const match = changelogMarkdown.match(/^## \[(\d+\.\d+\.\d+)\] - (.+)$/m);
  if (!match) {
    return null;
  }
  const [heading, version, status] = match;
  const anchor = heading
    .slice(3)
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s_-]/gu, '')
    .replace(/\s/g, '-');
  return { version, status: status.trim(), anchor };
}

/** The badge and the sentence for the home page: an unreleased entry is the next version. */
export function releaseLabel({ version, status }) {
  return status.toLowerCase() === 'unreleased'
    ? { badge: 'Next', text: `Next version ${version} (unreleased)` }
    : { badge: 'Latest', text: `Latest release ${version}` };
}
