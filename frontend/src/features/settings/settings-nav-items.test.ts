import { describe, expect, it } from "vitest";

import type { Role } from "@/lib/api";
import { ROLES, can, type Permission } from "@/lib/permissions";

import { SETTINGS_NAV_GROUPS, activeSettingsItem, visibleSettingsNav } from "./settings-nav-items";

/*
 * The role matrix of the backend's `app/core/permissions.py` (ROLE_PERMISSIONS), copied on
 * 2026-10-13. The UI hides what a role may not do; if this drifts from the server, a button shows
 * that only ever answers 404, or an allowed action disappears.
 */
const SERVER_MATRIX: Record<Role, readonly Permission[]> = {
  viewer: ["org:read", "project:read"],
  member: [
    "annotations:write",
    "datasets:write",
    "experiments:run",
    "export:create",
    "key:create",
    "key:revoke_own",
    "org:read",
    "project:read",
    "prompts:write",
    "scores:write",
  ],
  admin: [
    "alerts:write",
    "annotations:write",
    "audit:read",
    "datasets:write",
    "evaluators:write",
    "experiments:run",
    "export:create",
    "gateway:write",
    "insights:manage",
    "key:create",
    "key:revoke_any",
    "key:revoke_own",
    "member:manage",
    "org:read",
    "org:update",
    "project:delete",
    "project:read",
    "project:write",
    "prompts:write",
    "scores:write",
  ],
  owner: [
    "alerts:write",
    "annotations:write",
    "audit:read",
    "credentials:manage",
    "datasets:write",
    "evaluators:write",
    "experiments:run",
    "export:create",
    "gateway:write",
    "insights:manage",
    "key:create",
    "key:revoke_any",
    "key:revoke_own",
    "member:manage",
    "org:delete",
    "org:read",
    "org:security",
    "org:update",
    "project:delete",
    "project:read",
    "project:write",
    "prompts:write",
    "scores:write",
  ],
};

const ALL_PERMISSIONS = [...new Set(Object.values(SERVER_MATRIX).flat())];

describe("permission gating matches the server's role matrix", () => {
  it.each(ROLES)("%s", (role) => {
    const granted = ALL_PERMISSIONS.filter((permission) => can(role, permission)).sort();
    expect(granted).toEqual([...SERVER_MATRIX[role]].sort());
  });

  it("grants nothing to a signed-out or role-less visitor", () => {
    for (const permission of ALL_PERMISSIONS) {
      expect(can(null, permission)).toBe(false);
      expect(can(undefined, permission)).toBe(false);
    }
  });
});

function labels(role: Role | null): string[] {
  return visibleSettingsNav(role).flatMap((group) => group.items.map((item) => item.label));
}

describe("visibleSettingsNav", () => {
  it("hides the audit log from members and viewers, who get 404 from it", () => {
    expect(labels("viewer")).not.toContain("Audit log");
    expect(labels("member")).not.toContain("Audit log");
    expect(labels("admin")).toContain("Audit log");
  });

  it("always shows the personal pages, even without a role", () => {
    expect(labels(null)).toEqual(["Account", "Security", "Tokens"]);
    expect(visibleSettingsNav(null).map((group) => group.id)).toEqual(["personal"]);
  });

  it("shows every page to an owner", () => {
    const all = SETTINGS_NAV_GROUPS.flatMap((group) => group.items.map((item) => item.label));
    expect(labels("owner")).toEqual(all);
  });
});

describe("activeSettingsItem", () => {
  it("finds the page from the URL, including nested paths", () => {
    const groups = visibleSettingsNav("owner");
    expect(activeSettingsItem("/acme/web/settings/keys", groups)?.label).toBe("API keys");
    expect(activeSettingsItem("/acme/web/settings/audit/export", groups)?.label).toBe("Audit log");
    expect(activeSettingsItem("/acme/web/traces", groups)).toBeNull();
  });

  it("finds nothing for a page the role cannot see", () => {
    expect(activeSettingsItem("/acme/web/settings/audit", visibleSettingsNav("viewer"))).toBeNull();
  });
});
