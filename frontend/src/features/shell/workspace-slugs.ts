import type { QueryClient } from "@tanstack/react-query";

import { isTwoFactorRequired, orgsApi, queryKeys, type Me, type Project } from "@/lib/api";

import { buildSlugMap, type SlugEntry, type SlugMap } from "./slug-map";

/**
 * The org's projects, or null when the org is locked behind two-factor authentication (it answers
 * even its project list with `403 TWO_FACTOR_REQUIRED`). A cached list is used as it is
 * (`staleTime: "static"`); mounted queries refresh it.
 */
export async function orgProjectsOrNull(
  queryClient: QueryClient,
  orgId: string,
): Promise<Project[] | null> {
  try {
    return await queryClient.query({
      queryKey: queryKeys.org(orgId).projects,
      queryFn: () => orgsApi.projects(orgId),
      staleTime: "static",
    });
  } catch (error) {
    if (isTwoFactorRequired(error)) {
      return null;
    }
    throw error;
  }
}

/** Whether the org named in the URL has its project list to resolve slugs against. */
export type UrlOrgProjects = "listed" | "locked";

/**
 * Puts what slug URLs are resolved from into the query cache. The orgs come from `/me`, which the
 * authenticated layout has already loaded. The project list of the org named in the URL is
 * awaited, and its failure is the page's failure. The other orgs' lists only make links into
 * those orgs read as slugs, so they load in the background and never hold up or break the page;
 * until one arrives, links into that org keep ids and are redirected on arrival.
 *
 * `urlOrgId` is undefined, or not one of the person's orgs, when the URL names no known org.
 */
export async function ensureWorkspaceSlugs(
  queryClient: QueryClient,
  me: Me,
  urlOrgId: string | undefined,
): Promise<UrlOrgProjects> {
  const orgIds = me.memberships.map((membership) => membership.org.id);
  const otherOrgIds = orgIds.filter((orgId) => orgId !== urlOrgId);
  void Promise.allSettled(otherOrgIds.map((orgId) => loadInBackground(queryClient, orgId)));

  if (urlOrgId === undefined || !orgIds.includes(urlOrgId)) {
    return "listed";
  }
  const projects = await orgProjectsOrNull(queryClient, urlOrgId);
  return projects === null ? "locked" : "listed";
}

async function loadInBackground(queryClient: QueryClient, orgId: string): Promise<void> {
  // A list that is already known to be locked is not asked for again on every navigation.
  if (isTwoFactorRequired(queryClient.getQueryState(queryKeys.org(orgId).projects)?.error)) {
    return;
  }
  await orgProjectsOrNull(queryClient, orgId);
}

/**
 * The slug map as the query cache holds it right now. Synchronous, for the router's param
 * parsing and link building; a list that is not loaded yet resolves nothing for its org.
 */
export function readSlugMap(queryClient: QueryClient): SlugMap {
  const me = queryClient.getQueryData<Me | null>(queryKeys.me);
  const orgs: SlugEntry[] = (me?.memberships ?? []).map(({ org }) => ({
    id: org.id,
    slug: org.slug,
  }));
  const projectLists: [string, SlugEntry[]][] = [];
  for (const org of orgs) {
    const projects = queryClient.getQueryData<Project[]>(queryKeys.org(org.id).projects);
    if (projects) {
      projectLists.push([org.id, projects.map(({ id, slug }) => ({ id, slug }))]);
    }
  }
  return buildSlugMap(orgs, projectLists);
}
