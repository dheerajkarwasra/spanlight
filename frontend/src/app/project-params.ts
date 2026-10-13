import type { QueryClient } from "@tanstack/react-query";
import { notFound, redirect } from "@tanstack/react-router";

import {
  buildSlugMap,
  orgIdFromParam,
  orgIdOfPath,
  orgSlug,
  projectIdFromParam,
  projectSlug,
  workspacePathFor,
  type SlugMap,
} from "@/features/shell/slug-map";
import { ensureWorkspaceSlugs, readSlugMap } from "@/features/shell/workspace-slugs";
import type { Me } from "@/lib/api";

/*
 * The project layout's URL reads `/<orgSlug>/<projectSlug>/...`, while every page below it keeps
 * reading `params.orgId` and `params.projectId` as ids. The router's param parsing turns the URL's
 * slugs into ids, and its param stringifying turns ids back into slugs whenever a link, navigation
 * or redirect is built, so no call site knows about slugs.
 *
 * Both run synchronously while the router matches a URL or builds a location, so they read the
 * query cache directly. Routes are module-level, so the router factory binds the cache here.
 */

interface ProjectParams {
  orgId: string;
  projectId: string;
}

let slugSource: QueryClient | null = null;

/** Called by `createAppRouter` with the query client it puts in the router context. */
export function bindSlugSource(queryClient: QueryClient): void {
  slugSource = queryClient;
}

function currentSlugMap(): SlugMap {
  return slugSource ? readSlugMap(slugSource) : buildSlugMap([], []);
}

/**
 * `params` for the project layout route. A segment that does not resolve (the cache is not ready,
 * or it names nothing) passes through unchanged; `ensureCanonicalProjectUrl` deals with it.
 */
export const projectParams = {
  parse: (raw: ProjectParams): ProjectParams => {
    const map = currentSlugMap();
    const orgId = orgIdFromParam(map, raw.orgId) ?? raw.orgId;
    const projectId = projectIdFromParam(map, orgId, raw.projectId) ?? raw.projectId;
    return { orgId, projectId };
  },
  stringify: (params: ProjectParams): ProjectParams => {
    const map = currentSlugMap();
    return {
      orgId: orgSlug(map, params.orgId),
      projectId: projectSlug(map, params.orgId, params.projectId),
    };
  },
};

interface CanonicalUrlInput {
  queryClient: QueryClient;
  me: Me;
  params: ProjectParams;
  location: { pathname: string; searchStr: string; href: string };
}

/**
 * The project layout's `beforeLoad`. Loads the slug lists, then:
 * - a slug project of an org locked behind two-factor authentication goes to the account's
 *   Security page, the one place that stays open (an id link still reaches the shell's card);
 * - a segment that names no org or project the person can see is a not-found page;
 * - an old id link is replaced by the same location in slug form, search and hash kept;
 * - a URL matched before the lists were cached (its params still hold slugs) is matched again.
 */
export async function ensureCanonicalProjectUrl(input: CanonicalUrlInput): Promise<void> {
  const { queryClient, me, params, location } = input;
  const urlOrgId = orgIdOfPath(readSlugMap(queryClient), location.pathname);
  const urlOrgProjects = await ensureWorkspaceSlugs(queryClient, me, urlOrgId);
  const path = workspacePathFor(readSlugMap(queryClient), location.pathname);

  if (path.kind === "unknown") {
    if (urlOrgProjects === "locked") {
      throw redirect({ to: "/account/security", replace: true });
    }
    throw notFound();
  }
  if (path.kind === "redirect") {
    throw redirect({
      href: `${path.pathname}${location.searchStr}${hashOf(location.href)}`,
      replace: true,
    });
  }
  if (path.orgId !== params.orgId || path.projectId !== params.projectId) {
    throw redirect({ href: location.href, replace: true });
  }
}

/** The `#...` part of an href as it is in the URL (the parsed `location.hash` is decoded). */
function hashOf(href: string): string {
  const start = href.indexOf("#");
  return start === -1 ? "" : href.slice(start);
}
