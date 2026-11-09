import { errorMessage, isApiError, type Member, type Role } from "@/lib/api";
import { pluralize } from "@/lib/format";
import { ROLE_DESCRIPTIONS, ROLE_LABELS, ROLES } from "@/lib/permissions";

export const LAST_OWNER_MESSAGE =
  "An organization needs at least one owner. Promote someone else first.";

/** Error copy for member mutations; the last-owner conflict gets an actionable message. */
export function memberErrorMessage(error: unknown): string {
  if (isApiError(error) && error.code === "LAST_OWNER") {
    return LAST_OWNER_MESSAGE;
  }
  return errorMessage(error);
}

export function isRole(value: string): value is Role {
  return ROLES.some((role) => role === value);
}

/** Owners first, then admins, members and viewers; alphabetical within a role. */
export function sortMembers(members: readonly Member[]): Member[] {
  return [...members].sort((a, b) => {
    const byRole = ROLES.indexOf(a.role) - ROLES.indexOf(b.role);
    if (byRole !== 0) {
      return byRole;
    }
    return displayName(a).localeCompare(displayName(b));
  });
}

export function displayName(member: Member): string {
  return member.user.name || member.user.email;
}

export function memberRemovalKeyLines(readKeys: number): [string, string] {
  const keys =
    readKeys === 1 ? "1 API key they created loses" : `${readKeys} API keys they created lose`;
  return [
    readKeys === 0
      ? "No API key they created has read access."
      : `${keys} read access: read-only keys are revoked, ingest keys keep sending traces.`,
    "Gateway keys they created keep working and spending. Revoke any that should stop under Gateway.",
  ];
}

const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;

/** "Expires in 7 days", "Expires in 5 hours", or "Expired" once the date has passed. */
export function inviteExpiryLabel(expiresAt: string, now: Date = new Date()): string {
  const expires = new Date(expiresAt).getTime();
  if (Number.isNaN(expires)) {
    return "Expiry unknown";
  }
  const remaining = expires - now.getTime();
  if (remaining <= 0) {
    return "Expired";
  }
  if (remaining >= DAY_MS) {
    return `Expires in ${pluralize(Math.round(remaining / DAY_MS), "day")}`;
  }
  if (remaining >= HOUR_MS) {
    return `Expires in ${pluralize(Math.round(remaining / HOUR_MS), "hour")}`;
  }
  return "Expires in less than an hour";
}

/** "a member", "an admin": the role name with its indefinite article, lowercase. */
export function roleWithArticle(role: Role): string {
  const label = ROLE_LABELS[role].toLowerCase();
  const article = /^[aeiou]/.test(label) ? "an" : "a";
  return `${article} ${label}`;
}

/** "Member: view data and create or revoke their own API keys." */
export function roleHint(role: Role): string {
  const description = ROLE_DESCRIPTIONS[role];
  return `${ROLE_LABELS[role]}: ${description.charAt(0).toLowerCase()}${description.slice(1)}`;
}

const ROLE_CHANGE_DESCRIPTIONS: Record<Role, string> = {
  owner: "Owners get full access, including deleting the organization.",
  admin: "Admins manage projects, members, all API keys and the audit log.",
  member:
    "Members create their own API keys and manage prompts, datasets, scores and experiments. No member or project management.",
  viewer: "Viewers only read traces and metrics.",
};

export function memberRoleDescription(current: Role, next: Role): string {
  const direction =
    ROLES.indexOf(next) < ROLES.indexOf(current)
      ? "Takes effect at once. Confirm it's you first."
      : "They lose their current access at once. Confirm it's you first.";
  return `${ROLE_CHANGE_DESCRIPTIONS[next]} ${direction}`;
}
