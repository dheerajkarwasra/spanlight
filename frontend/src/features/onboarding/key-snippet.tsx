import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { useCurrentOrg, useProjectParams } from "@/features/shell";
import { projectsApi, queryKeys, type ApiKey } from "@/lib/api";

const KEY_SCHEME = "spl_live_";

/** In use: not revoked and not past its expiry. */
function isActive(key: ApiKey, now: number): boolean {
  return (
    key.revoked_at === null && (key.expires_at === null || new Date(key.expires_at).getTime() > now)
  );
}

/** "SPANLIGHT_API_KEY=spl_live_ABCD…" for the newest active key, or null when there is none. */
function newestKeyLine(keys: readonly ApiKey[]): string | null {
  const now = Date.now();
  const newest = keys
    .filter((key) => isActive(key, now))
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0];
  if (!newest) {
    return null;
  }
  const prefix = newest.prefix.startsWith(KEY_SCHEME) ? newest.prefix : KEY_SCHEME + newest.prefix;
  return `SPANLIGHT_API_KEY=${prefix}…`;
}

/**
 * A one-line dark snippet built around the project's newest active key prefix (the secret is never
 * retrievable), or a link to create a key when there is none. Nothing when the keys can't be
 * listed (a viewer's 403: they can't create one either).
 */
export function KeySnippet({ code }: { code: (keyLine: string) => string }) {
  const { orgId, projectId } = useProjectParams();
  const org = useCurrentOrg();
  const keysQuery = useQuery({
    queryKey: queryKeys.project(projectId).keys,
    queryFn: () => projectsApi.keys(projectId),
    // The demo visitor can't create keys, like LiveDemoButton it shows nothing there.
    enabled: !org?.is_demo,
  });

  if (org?.is_demo || !keysQuery.isSuccess) {
    return null;
  }
  const keyLine = newestKeyLine(keysQuery.data);
  if (!keyLine) {
    return (
      <Link
        to="/onboarding"
        search={{ org: orgId, project: projectId }}
        className="text-sm font-semibold text-foreground underline underline-offset-4"
      >
        Create an API key
      </Link>
    );
  }
  return (
    <pre
      tabIndex={0}
      aria-label="Your API key setting"
      className="w-full rounded-tile bg-hero-card px-3 py-2.5 text-left font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap text-hero-card-foreground dark:border dark:border-border"
    >
      <code>{code(keyLine)}</code>
    </pre>
  );
}
