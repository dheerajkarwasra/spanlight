import { isApiError, type Me, type Org } from "@/lib/api";

/** Why the server refused to turn "Require two-factor authentication" on. */
export type RequireTwoFactorProblem = "not-configured" | "not-enabled";

/**
 * `409 NOT_CONFIGURED`: the server has no `CREDENTIALS_KEYS`, so no authenticator secret can be
 * stored. `409 TWO_FACTOR_NOT_ENABLED`: the owner has no two-factor authentication of their own.
 * Anything else is not an answer the card can explain, so it is null.
 */
export function classifyRequireTwoFactorError(error: unknown): RequireTwoFactorProblem | null {
  if (!isApiError(error) || error.status !== 409) {
    return null;
  }
  if (error.code === "NOT_CONFIGURED") {
    return "not-configured";
  }
  if (error.code === "TWO_FACTOR_NOT_ENABLED") {
    return "not-enabled";
  }
  return null;
}

interface RequirementState {
  required: boolean;
  /** `me.totp_enabled`: the owner's own two-factor authentication. */
  ownerHasTwoFactor: boolean;
}

/**
 * Whether the owner can't turn the requirement on yet because their own account lacks two-factor
 * authentication. It follows `me` alone, so it clears as soon as `me` says the owner turned it on
 * (a `409 TWO_FACTOR_NOT_ENABLED` first corrects `me`, see `withOwnTwoFactor`). Turning it off is
 * never blocked.
 */
export function mustEnableOwnTwoFactor({ required, ownerHasTwoFactor }: RequirementState): boolean {
  return !required && !ownerHasTwoFactor;
}

export function securitySubtitle(orgName: string): string {
  return `Rules that apply to everyone in ${orgName}.`;
}

/**
 * The line under "Require two-factor authentication". Owners see what flipping the switch does;
 * everyone else sees the current rule and who can change it.
 */
export function requirementDescription(
  orgName: string,
  required: boolean,
  canChange: boolean,
  sayWhoCanChange = true,
): string {
  if (!canChange) {
    const rule = required
      ? `Everyone in ${orgName} needs two-factor authentication to open it.`
      : "Members can choose whether to use two-factor authentication.";
    return sayWhoCanChange ? `${rule} Only owners can change this.` : rule;
  }
  const effect = `without two-factor authentication can't open ${orgName} until they turn it on in Settings › Security.`;
  return required ? `Members ${effect}` : `When it's on, members ${effect}`;
}

export function requireDialogBody(orgName: string): string {
  return `Members who haven't turned it on will be blocked from ${orgName} until they do. You can turn this off at any time.`;
}

/** The toast after the switch saved. */
export function requirementSavedMessage(orgName: string, required: boolean): string {
  return required
    ? `Two-factor authentication is now required in ${orgName}.`
    : `Two-factor authentication is no longer required in ${orgName}.`;
}

/** The cached `/me` with the owner's own two-factor authentication set to what the server said. */
export function withOwnTwoFactor(me: Me, enabled: boolean): Me {
  return { ...me, totp_enabled: enabled };
}

/** The cached `/me` without an organization that was deleted. */
export function withoutOrg(me: Me, orgId: string): Me {
  return { ...me, memberships: me.memberships.filter((membership) => membership.org.id !== orgId) };
}

/** The cached `/me` with the organization replaced by its saved version (name, `require_2fa`). */
export function withUpdatedOrg(me: Me, org: Org): Me {
  return {
    ...me,
    memberships: me.memberships.map((membership) =>
      membership.org.id === org.id ? { ...membership, org } : membership,
    ),
  };
}
