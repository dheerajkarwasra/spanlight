import type { QueryClient } from "@tanstack/react-query";

import type { Me } from "@/lib/api";
import { readLastProject } from "@/lib/last-project";

import { orgProjectsOrNull } from "./workspace-slugs";

type HomeDestination =
  | { to: "/onboarding"; search: { org?: string } }
  | { to: "/account/security" }
  | { to: "/$orgId/$projectId/overview"; params: { orgId: string; projectId: string } };

/**
 * Where "/" should send a signed-in user: their last project if they still
 * have access, otherwise the first project of their first org, otherwise
 * onboarding.
 *
 * An org that requires two-factor authentication answers `403 TWO_FACTOR_REQUIRED` to everyone
 * who has not turned it on, even for its list of projects. Such an org is skipped, so a person who
 * belongs to another one still gets in (or, if that one has no project yet, onboards in it). Only
 * when every org is locked is there nothing to show, so they go to their account's Security page,
 * the one place that stays open, to turn it on.
 */
export async function resolveHomeDestination(
  queryClient: QueryClient,
  me: Me,
): Promise<HomeDestination> {
  if (me.memberships.length === 0) {
    return { to: "/onboarding", search: {} };
  }

  const last = readLastProject();
  const orgIds = me.memberships.map((membership) => membership.org.id);
  const orderedOrgIds =
    last && orgIds.includes(last.orgId)
      ? [last.orgId, ...orgIds.filter((id) => id !== last.orgId)]
      : orgIds;

  let locked = false;
  // The first org that opened but has no project to land on, for onboarding below.
  let openOrgId: string | null = null;
  for (const orgId of orderedOrgIds) {
    const projects = await orgProjectsOrNull(queryClient, orgId);
    if (projects === null) {
      locked = true;
      continue;
    }
    openOrgId ??= orgId;
    const preferred = projects.find((project) => project.id === last?.projectId) ?? projects[0];
    if (preferred) {
      return {
        to: "/$orgId/$projectId/overview",
        params: { orgId, projectId: preferred.id },
      };
    }
  }

  if (locked && openOrgId === null) {
    return { to: "/account/security" };
  }
  return { to: "/onboarding", search: { org: openOrgId ?? orderedOrgIds[0] } };
}
