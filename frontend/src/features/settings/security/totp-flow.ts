import { errorMessage, isApiError, type Membership } from "@/lib/api";

import { RECOVERY_CODE_TOTAL } from "./recovery-codes";

/** What a failed two-factor call means for the screen that made it. */
export type TotpFailure =
  | { kind: "invalid-code" }
  | { kind: "invalid-password" }
  | { kind: "not-configured"; message: string }
  | { kind: "email-unverified" }
  | { kind: "already-enabled" }
  | { kind: "not-enabled" }
  | { kind: "rate-limited" }
  | { kind: "other"; message: string };

export function classifyTotpError(error: unknown): TotpFailure {
  if (isApiError(error)) {
    if (error.status === 422 && error.code === "INVALID_TOTP_CODE") {
      return { kind: "invalid-code" };
    }
    if (error.status === 422 && error.code === "INVALID_PASSWORD") {
      return { kind: "invalid-password" };
    }
    if (error.status === 429) {
      return { kind: "rate-limited" };
    }
    if (error.status === 409) {
      switch (error.code) {
        case "NOT_CONFIGURED":
          return { kind: "not-configured", message: error.message };
        case "EMAIL_UNVERIFIED":
          return { kind: "email-unverified" };
        case "TOTP_ALREADY_ENABLED":
          return { kind: "already-enabled" };
        case "TOTP_NOT_ENABLED":
          return { kind: "not-enabled" };
      }
    }
  }
  return { kind: "other", message: errorMessage(error) };
}

/** The title of the callout that says the server cannot do this (ruling 2). */
export const NOT_AVAILABLE_TITLE = "Not available on this server";

/** The error line under a code that was refused. */
export const WRONG_CODE_MESSAGE = "That code didn't work. Check your device's clock and try again.";
export const WRONG_RECOVERY_CODE_MESSAGE =
  "That recovery code didn't work. Check it and try again.";
export const RATE_LIMITED_MESSAGE = "Too many attempts. Wait 15 minutes, then try again.";

/** Whether what was typed is a six-digit authenticator code rather than a recovery code. */
export function isAuthenticatorCode(value: string): boolean {
  return /^\d{6}$/.test(value.trim());
}

/** The warning on the "turn off" dialog when an organization requires two-factor authentication. */
export function requiredByOrgsWarning(orgNames: readonly string[]): string | null {
  if (orgNames.length === 0) {
    return null;
  }
  if (orgNames.length === 1) {
    const [name] = orgNames;
    return `${name} requires two-factor authentication. Turning it off blocks your access to ${name} until you turn it on again.`;
  }
  const list = new Intl.ListFormat("en-US", { style: "long", type: "conjunction" }).format(
    orgNames,
  );
  return `${list} require two-factor authentication. Turning it off blocks your access to them until you turn it on again.`;
}

/** The names of the organizations the person belongs to that require two-factor authentication. */
export function orgsRequiringTwoFactor(memberships: readonly Membership[]): string[] {
  return memberships.filter(({ org }) => org.require_2fa).map(({ org }) => org.name);
}

/** At or below this many recovery codes the card warns. */
export const LOW_RECOVERY_CODES = 2;

/** The card's warning when few recovery codes remain, or null while there are enough. */
export function lowRecoveryCodesWarning(remaining: number): string | null {
  if (remaining > LOW_RECOVERY_CODES) {
    return null;
  }
  const how =
    remaining <= 0
      ? "No recovery codes left."
      : remaining === 1
        ? "Only 1 recovery code left."
        : `Only ${remaining} recovery codes left.`;
  return `${how} Turn two-factor authentication off and on again to get a new set.`;
}

/** "8 of 10 left. …": the line in the card's recovery codes tile. */
export function recoveryCodesSummary(remaining: number): string {
  const left = Math.min(Math.max(remaining, 0), RECOVERY_CODE_TOTAL);
  return `${left} of ${RECOVERY_CODE_TOTAL} left. Each code works once and gets you in if you lose your authenticator app.`;
}

/** `ABCDEFGH…` → `ABCD EFGH …`: the setup key in groups of four, easier to read and type. */
export function groupSecret(secret: string): string {
  return secret.replace(/\s+/g, "").replace(/(.{4})(?=.)/g, "$1 ");
}
