import type { Role } from "@/lib/api";

/** Mirrors backend `core/permissions.py`. The server is authoritative; this only drives UI. */
export type Permission =
  | "org:read"
  | "project:read"
  | "key:create"
  | "key:revoke_own"
  | "key:revoke_any"
  | "project:write"
  | "project:delete"
  | "member:manage"
  | "audit:read"
  | "org:update"
  | "org:delete"
  | "org:security"
  | "export:create"
  | "gateway:write"
  | "credentials:manage"
  | "billing_sources:manage"
  | "alerts:write"
  | "insights:manage"
  | "insights:feedback"
  | "scores:write"
  | "annotations:write"
  | "prompts:write"
  | "datasets:write"
  | "experiments:run"
  | "evaluators:write";

const VIEWER: Permission[] = ["org:read", "project:read"];
const MEMBER: Permission[] = [
  ...VIEWER,
  "key:create",
  "key:revoke_own",
  "export:create",
  "scores:write",
  "annotations:write",
  "prompts:write",
  "datasets:write",
  "experiments:run",
  "insights:feedback",
];
const ADMIN: Permission[] = [
  ...MEMBER,
  "key:revoke_any",
  "project:write",
  "project:delete",
  "member:manage",
  "org:update",
  "audit:read",
  "gateway:write",
  "alerts:write",
  "insights:manage",
  "evaluators:write",
  "billing_sources:manage",
];
const OWNER: Permission[] = [...ADMIN, "org:delete", "org:security", "credentials:manage"];

const ROLE_PERMISSIONS: Record<Role, ReadonlySet<Permission>> = {
  viewer: new Set(VIEWER),
  member: new Set(MEMBER),
  admin: new Set(ADMIN),
  owner: new Set(OWNER),
};

export function can(role: Role | null | undefined, permission: Permission): boolean {
  if (!role) {
    return false;
  }
  return ROLE_PERMISSIONS[role].has(permission);
}

export const ROLES: Role[] = ["owner", "admin", "member", "viewer"];

export const ROLE_LABELS: Record<Role, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  viewer: "Viewer",
};

export const ROLE_DESCRIPTIONS: Record<Role, string> = {
  owner: "Full access, including deleting the organization.",
  admin: "Manage projects, members, all API keys and the audit log.",
  member:
    "Create their own API keys, score traces, review queues, and manage prompts, datasets, playground runs and experiments.",
  viewer: "Read-only access to traces and metrics.",
};

/**
 * The one sentence for a write the caller's role does not allow, e.g. "Your role can't change
 * prompts. Ask an admin or the owner for member access." Only for member-level writes.
 */
export function roleBlockedCopy(action: string): string {
  return `Your role can't ${action}. Ask an admin or the owner for member access.`;
}

const ROLE_PLURALS: Record<Role, string> = {
  viewer: "viewers",
  member: "members",
  admin: "admins",
  owner: "owners",
};

/** The roles that hold `permission`, lowest first, as prose: "admins and owners". */
export function rolesWith(permission: Permission): string {
  const names = [...ROLES]
    .reverse()
    .filter((role) => can(role, permission))
    .map((role) => ROLE_PLURALS[role]);
  return names.length > 1
    ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`
    : (names[0] ?? "");
}

/**
 * The one banner of a page the role can read but not change (Figma "Settings v4 — Access
 * states"): "You have view-only access. Admins and owners can change organization settings."
 */
export function viewOnlyCopy(what: string, permission: Permission): string {
  const who = rolesWith(permission);
  return `You have view-only access. ${who.charAt(0).toUpperCase()}${who.slice(1)} can change ${what}.`;
}
