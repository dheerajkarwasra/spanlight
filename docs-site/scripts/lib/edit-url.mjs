/**
 * The "Edit page" link for a generated page: the repository file it is built from, or `false`
 * when no single file is its source (pages rendered from the OpenAPI document).
 */
export function editUrlFor(repoUrl, sourcePath) {
  if (!sourcePath) {
    return false;
  }
  return `${repoUrl}/edit/main/${sourcePath.replace(/^\/+/, '')}`;
}
