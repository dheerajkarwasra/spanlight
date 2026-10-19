import { isApiError, type Credential, type ProviderKind, type Route } from "@/lib/api";

/** Hosts the gateway calls when a credential has no base URL of its own. */
const DEFAULT_HOSTS: Record<Exclude<ProviderKind, "openai_compatible">, string> = {
  openai: "api.openai.com",
  anthropic: "api.anthropic.com",
};

/**
 * What the BASE URL column shows. No custom URL is a known default, not a missing value, so it
 * reads "Default · host". An OpenAI-compatible credential always has a URL; if one were missing
 * the caller shows the unknown placeholder, so this returns null.
 */
export function baseUrlLabel(credential: Credential): string | null {
  if (credential.base_url) {
    return credential.base_url;
  }
  if (credential.provider === "openai_compatible") {
    return null;
  }
  return `Default · ${DEFAULT_HOSTS[credential.provider]}`;
}

export type CheckState =
  { kind: "working"; checkedAt: string } | { kind: "failing"; error: string } | { kind: "never" };

/** The last check outcome. A recorded error wins: a failed call after a good check is failing. */
export function checkState(credential: Credential): CheckState {
  if (credential.last_error) {
    return { kind: "failing", error: credential.last_error };
  }
  if (credential.last_checked_at) {
    return { kind: "working", checkedAt: credential.last_checked_at };
  }
  return { kind: "never" };
}

/** A route of the current project that sends calls through a credential. */
export interface RouteUse {
  id: string;
  name: string;
}

/**
 * Credential id to the routes of this project that target it, in the routes' order. Credentials
 * are shared by the whole org, so a route in another project can also use one; the server answers
 * `CREDENTIAL_IN_USE` for those, and the delete dialog explains it.
 */
export function routeUsesByCredential(routes: readonly Route[]): Map<string, RouteUse[]> {
  const uses = new Map<string, RouteUse[]>();
  for (const route of routes) {
    for (const target of route.config.targets) {
      const list = uses.get(target.credential_id) ?? [];
      if (!list.some((use) => use.id === route.id)) {
        list.push({ id: route.id, name: route.name });
      }
      uses.set(target.credential_id, list);
    }
  }
  return uses;
}

/** "Used by route a", "Used by routes a and b", "Used by routes a, b and c". Null when unused. */
export function usedByLabel(uses: readonly RouteUse[] | undefined): string | null {
  if (!uses || uses.length === 0) {
    return null;
  }
  const names = uses.map((use) => use.name);
  if (names.length === 1) {
    return `Used by route ${names[0]}`;
  }
  const last = names[names.length - 1];
  return `Used by routes ${names.slice(0, -1).join(", ")} and ${last}`;
}

/** `409 NOT_CONFIGURED`: the server has no `CREDENTIALS_KEYS`, so no key can be stored. */
export function isNotConfigured(error: unknown): boolean {
  return isApiError(error) && error.status === 409 && error.code === "NOT_CONFIGURED";
}

/** `409 CREDENTIAL_IN_USE`; the error's message is "{name} is used by route {route}. …". */
export function isCredentialInUse(error: unknown): boolean {
  return isApiError(error) && error.status === 409 && error.code === "CREDENTIAL_IN_USE";
}

/** `409 CREDENTIAL_NAME_TAKEN`. */
export function isNameTaken(error: unknown): boolean {
  return isApiError(error) && error.status === 409 && error.code === "CREDENTIAL_NAME_TAKEN";
}

/**
 * The route named by a `CREDENTIAL_IN_USE` message, matched against the project's routes so the
 * dialog can link to it. Null when the route belongs to another project.
 */
export function routeNamedIn(message: string, routes: readonly Route[]): Route | null {
  const match = / is used by route (.+)\. Remove it from the route first\.$/.exec(message);
  const name = match?.[1];
  return routes.find((route) => route.name === name) ?? null;
}

export const NOT_CONFIGURED_COPY =
  "Provider credentials are encrypted with a server key that isn't set. Ask your administrator to set CREDENTIALS_KEYS.";
