import type { ApiKey, KeyScope } from "@/lib/api";

import { expiryState } from "./tokens/expiry";

const KEY_PREFIX = "spl_live_";

export function hasReadKeyScope(scopes: readonly KeyScope[]): boolean {
  return scopes.includes("traces:read") || scopes.includes("prompts:read");
}

/**
 * The visible part of a key, e.g. "spl_live_ABCD…". The API returns the lookup
 * prefix; it may or may not already include the "spl_live_" scheme.
 */
export function formatKeyPrefix(prefix: string): string {
  const withScheme = prefix.startsWith(KEY_PREFIX) ? prefix : `${KEY_PREFIX}${prefix}`;
  return `${withScheme}…`;
}

export function isRevoked(key: ApiKey): boolean {
  return key.revoked_at !== null;
}

function newestFirst(a: string, b: string): number {
  return new Date(b).getTime() - new Date(a).getTime();
}

export type KeyStatus = "active" | "expiring" | "expired" | "revoked";

/** `expiring` is a key still in use that ends within a week (Figma "Settings/Key row v2"). */
export function keyStatus(key: ApiKey, now: Date = new Date()): KeyStatus {
  if (isRevoked(key)) {
    return "revoked";
  }
  const state = expiryState(key.expires_at, now);
  if (state === "expired") {
    return "expired";
  }
  return state === "soon" ? "expiring" : "active";
}

/**
 * Keys in use first (newest first), then expired keys (newest first), then revoked keys (most
 * recently revoked first): the order a person cares about, with the ones that no longer work last.
 */
export function sortApiKeys(keys: readonly ApiKey[]): ApiKey[] {
  const now = new Date();
  const inUse: ApiKey[] = [];
  const expired: ApiKey[] = [];
  const revoked: ApiKey[] = [];
  for (const key of keys) {
    const status = keyStatus(key, now);
    (status === "revoked" ? revoked : status === "expired" ? expired : inUse).push(key);
  }
  inUse.sort((a, b) => newestFirst(a.created_at, b.created_at));
  expired.sort((a, b) => newestFirst(a.created_at, b.created_at));
  revoked.sort((a, b) => newestFirst(a.revoked_at ?? a.created_at, b.revoked_at ?? b.created_at));
  return [...inUse, ...expired, ...revoked];
}

export interface RevokeAbility {
  canRevokeAny: boolean;
  canRevokeOwn: boolean;
  userId: string;
}

export type RevokeDecision = { allowed: true } | { allowed: false; reason: string };

/** Mirrors the server rule: `key:revoke_any`, or `key:revoke_own` for keys you created. */
export function revokeDecision(key: ApiKey, ability: RevokeAbility): RevokeDecision {
  if (ability.canRevokeAny) {
    return { allowed: true };
  }
  if (ability.canRevokeOwn) {
    if (key.created_by?.id === ability.userId) {
      return { allowed: true };
    }
    return { allowed: false, reason: "You can only revoke keys you created." };
  }
  return { allowed: false, reason: "Viewers can't revoke API keys." };
}
