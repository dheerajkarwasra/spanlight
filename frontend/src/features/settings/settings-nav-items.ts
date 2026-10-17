import type { Role } from "@/lib/api";
import { can, type Permission } from "@/lib/permissions";

/** Every settings page the nav can link to. A page's task adds its route here. */
export type SettingsPath =
  | "/$orgId/$projectId/settings/project"
  | "/$orgId/$projectId/settings/keys"
  | "/$orgId/$projectId/settings/exports"
  | "/$orgId/$projectId/settings/organization"
  | "/$orgId/$projectId/settings/members"
  | "/$orgId/$projectId/settings/billing-sources"
  | "/$orgId/$projectId/settings/audit"
  | "/$orgId/$projectId/settings/account"
  | "/$orgId/$projectId/settings/security"
  | "/$orgId/$projectId/settings/tokens";

export interface SettingsNavItem {
  label: string;
  to: SettingsPath;
  /**
   * The read permission the page needs. The item shows only for roles that hold it; without one the
   * page is about the person, not the organization, and everyone signed in sees it.
   */
  permission?: Permission;
}

export interface SettingsNavGroup {
  id: "project" | "organization" | "personal";
  label: string;
  items: SettingsNavItem[];
}

/**
 * The grouped settings navigation (Figma "Settings/Nav v2"): Project, Organization and Personal.
 * It is data, so a new page is one entry: Exports goes under Project and Tokens under Personal.
 */
export const SETTINGS_NAV_GROUPS: readonly SettingsNavGroup[] = [
  {
    id: "project",
    label: "Project",
    items: [
      { label: "Project", to: "/$orgId/$projectId/settings/project", permission: "project:read" },
      { label: "API keys", to: "/$orgId/$projectId/settings/keys", permission: "project:read" },
      { label: "Exports", to: "/$orgId/$projectId/settings/exports", permission: "project:read" },
    ],
  },
  {
    id: "organization",
    label: "Organization",
    items: [
      {
        label: "Organization",
        to: "/$orgId/$projectId/settings/organization",
        permission: "org:read",
      },
      { label: "Members", to: "/$orgId/$projectId/settings/members", permission: "org:read" },
      {
        label: "Billing sources",
        to: "/$orgId/$projectId/settings/billing-sources",
        permission: "org:read",
      },
      { label: "Audit log", to: "/$orgId/$projectId/settings/audit", permission: "audit:read" },
    ],
  },
  {
    id: "personal",
    label: "Personal",
    items: [
      { label: "Account", to: "/$orgId/$projectId/settings/account" },
      { label: "Security", to: "/$orgId/$projectId/settings/security" },
      { label: "Tokens", to: "/$orgId/$projectId/settings/tokens" },
    ],
  },
];

/** The groups a role can see: items it may not read are dropped, and so are emptied groups. */
export function visibleSettingsNav(
  role: Role | null,
  groups: readonly SettingsNavGroup[] = SETTINGS_NAV_GROUPS,
): SettingsNavGroup[] {
  return groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => !item.permission || can(role, item.permission)),
    }))
    .filter((group) => group.items.length > 0);
}

/** `/o/p/settings/keys` → `keys`: the segment after `settings`, or null off the settings pages. */
function settingsSegment(path: string): string | null {
  const segments = path.split("/");
  const index = segments.lastIndexOf("settings");
  return index === -1 ? null : (segments[index + 1] ?? null);
}

/** The nav item for the page at `pathname`, for the mobile picker's label. */
export function activeSettingsItem(
  pathname: string,
  groups: readonly SettingsNavGroup[],
): SettingsNavItem | null {
  const current = settingsSegment(pathname);
  if (current === null) {
    return null;
  }
  for (const group of groups) {
    const match = group.items.find((item) => settingsSegment(item.to) === current);
    if (match) {
      return match;
    }
  }
  return null;
}
