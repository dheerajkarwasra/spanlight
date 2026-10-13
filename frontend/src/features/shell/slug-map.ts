/**
 * Org and project slugs in dashboard URLs: `/<orgSlug>/<projectSlug>/...` instead of ids.
 *
 * Pure lookups over the orgs the person belongs to and the project lists loaded so far. A URL
 * segment may hold a slug or an id (old links); both resolve to the id. Org slugs are unique
 * across the server, project slugs within their org, and neither changes after creation.
 */

export interface SlugEntry {
  id: string;
  slug: string;
}

export interface SlugMap {
  orgs: readonly SlugEntry[];
  /** Projects per org id. An org whose list is not loaded (or is locked) is absent. */
  projects: ReadonlyMap<string, readonly SlugEntry[]>;
}

/**
 * First path segments that already mean something: the dashboard's own top-level pages and the
 * paths the web server hands to the backend. An org with one of these slugs keeps its id in URLs.
 * The backend never hands these out as org slugs: keep this list identical to `RESERVED_ORG_SLUGS`
 * in `backend/app/services/slugs.py` (a backend test compares the two).
 */
export const RESERVED_FIRST_SEGMENTS: ReadonlySet<string> = new Set([
  "account",
  "api",
  "assets",
  "docs",
  "forgot-password",
  "gw",
  "health",
  "invite",
  "login",
  "onboarding",
  "reset-password",
  "signup",
  "v1",
  "verify-email",
]);

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function buildSlugMap(
  orgs: readonly SlugEntry[],
  projectLists: Iterable<readonly [orgId: string, projects: readonly SlugEntry[]]>,
): SlugMap {
  return { orgs, projects: new Map(projectLists) };
}

/**
 * The id a URL segment stands for: a known id or slug, or any uuid (passed through so the server
 * decides, e.g. for an org whose projects are locked behind two-factor authentication).
 */
function idFromParam(entries: readonly SlugEntry[], param: string): string | undefined {
  const entry = entries.find((candidate) => candidate.id === param || candidate.slug === param);
  if (entry) {
    return entry.id;
  }
  return UUID.test(param) ? param : undefined;
}

export function orgIdFromParam(map: SlugMap, param: string): string | undefined {
  return idFromParam(map.orgs, param);
}

export function projectIdFromParam(map: SlugMap, orgId: string, param: string): string | undefined {
  return idFromParam(map.projects.get(orgId) ?? [], param);
}

/** The org's slug for a URL, or the value itself when it is not a known org id. */
export function orgSlug(map: SlugMap, orgId: string): string {
  const slug = map.orgs.find((org) => org.id === orgId)?.slug;
  if (slug === undefined || RESERVED_FIRST_SEGMENTS.has(slug)) {
    return orgId;
  }
  return slug;
}

/** The project's slug for a URL, or the value itself when it is not a known project of the org. */
export function projectSlug(map: SlugMap, orgId: string, projectId: string): string {
  const projects = map.projects.get(orgId) ?? [];
  return projects.find((project) => project.id === projectId)?.slug ?? projectId;
}

/** A malformed escape (`%E0`) cannot name an org or project; keep it as typed. */
function safeDecode(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export type WorkspacePath =
  /** One of the two segments names no org or project the person can see. */
  | { kind: "unknown" }
  /** Already in slug form (or in id form where no slug is known). */
  | { kind: "canonical"; orgId: string; projectId: string }
  /** An old id link: the same location with slugs. */
  | { kind: "redirect"; orgId: string; projectId: string; pathname: string };

/** The org a `/<org>/<project>/...` pathname names, slug or id; undefined when it names none. */
export function orgIdOfPath(map: SlugMap, pathname: string): string | undefined {
  const [, orgParam = ""] = pathname.split("/");
  return orgIdFromParam(map, safeDecode(orgParam));
}

/** Reads the workspace from a `/<org>/<project>/...` pathname and says whether it is canonical. */
export function workspacePathFor(map: SlugMap, pathname: string): WorkspacePath {
  const [, orgParam = "", projectParam = "", ...rest] = pathname.split("/");
  const orgId = orgIdFromParam(map, safeDecode(orgParam));
  if (orgId === undefined) {
    return { kind: "unknown" };
  }
  const projectId = projectIdFromParam(map, orgId, safeDecode(projectParam));
  if (projectId === undefined) {
    return { kind: "unknown" };
  }

  const orgSegment = encodeURIComponent(orgSlug(map, orgId));
  const projectSegment = encodeURIComponent(projectSlug(map, orgId, projectId));
  if (orgSegment === orgParam && projectSegment === projectParam) {
    return { kind: "canonical", orgId, projectId };
  }
  const pathnameWithSlugs = ["", orgSegment, projectSegment, ...rest].join("/");
  return { kind: "redirect", orgId, projectId, pathname: pathnameWithSlugs };
}
