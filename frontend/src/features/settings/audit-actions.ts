import type { AuditEvent } from "@/lib/api";

/**
 * Human labels for audit actions. The backend records present-tense verbs
 * ("key.create"); past-tense aliases are accepted too so older or future
 * spellings still read well. Unknown actions fall back to the raw string.
 */
const AUDIT_ACTION_LABELS: ReadonlyMap<string, string> = new Map(
  Object.entries({
    "org.create": "Created organization",
    "org.created": "Created organization",
    "org.update": "Updated organization",
    "org.updated": "Updated organization",
    "org.delete": "Deleted organization",
    "org.deleted": "Deleted organization",
    "project.create": "Created project",
    "project.created": "Created project",
    "project.update": "Updated project settings",
    "project.updated": "Updated project settings",
    "project.delete": "Deleted project",
    "project.deleted": "Deleted project",
    "key.create": "Created API key",
    "key.created": "Created API key",
    "key.revoke": "Revoked API key",
    "key.revoked": "Revoked API key",
    "member.add": "Added member",
    "member.added": "Added member",
    "member.role_change": "Changed member role",
    "member.role_changed": "Changed member role",
    "member.remove": "Removed member",
    "member.removed": "Removed member",
    "invite.create": "Created invite link",
    "invite.created": "Created invite link",
    "invite.accept": "Accepted invite",
    "invite.accepted": "Accepted invite",
    "invite.revoke": "Revoked invite link",
    "invite.revoked": "Revoked invite link",
    "session.revoke": "Revoked a session",
    "session.revoked": "Revoked a session",
    "user.login": "Signed in",
    "user.logout": "Signed out",
    "user.signup": "Created account",
    "user.password_reset": "Reset password",
    "user.oauth_link": "Connected a sign-in provider",
    "user.oauth_unlink": "Disconnected a sign-in provider",
    "user.totp_enable": "Turned on two-factor authentication",
    "user.totp_disable": "Turned off two-factor authentication",
    "credential.create": "Added provider credential",
    "credential.rotate": "Rotated provider credential",
    "credential.delete": "Deleted provider credential",
    "gateway_route.create": "Created gateway route",
    "gateway_route.update": "Updated gateway route",
    "gateway_route.revert": "Reverted gateway route",
    "gateway_route.delete": "Deleted gateway route",
    "gateway_key.create": "Created gateway key",
    "gateway_key.update": "Updated gateway key",
    "gateway_key.revoke": "Revoked gateway key",
    "fault_profile.create": "Created fault profile",
    "fault_profile.update": "Updated fault profile",
    "fault_profile.delete": "Deleted fault profile",
    "price_override.create": "Created price override",
    "price_override.delete": "Deleted price override",
    "gateway_cache.purge": "Purged gateway cache",
    "alert_channel.create": "Added alert channel",
    "alert_channel.update": "Updated alert channel",
    "alert_channel.delete": "Deleted alert channel",
    "alert_channel.test": "Sent test notification",
    "alert_channel.retry_delivery": "Retried alert delivery",
    "alert_rule.create": "Created alert rule",
    "alert_rule.update": "Updated alert rule",
    "alert_rule.delete": "Deleted alert rule",
    "alert_rule.mute": "Muted alert rule",
    "alert_event.acknowledge": "Acknowledged alert",
    "budget.create": "Created budget",
    "budget.update": "Updated budget",
    "budget.delete": "Deleted budget",
    "dataset.lock": "Locked dataset",
    "dataset.unlock": "Unlocked dataset",
    "user.session_revoke": "Ended a session",
    "token.create": "Created access token",
    "token.revoke": "Revoked access token",
    "export.create": "Requested an export",
    "audit.export": "Downloaded the audit log",
    "access.denied": "Change refused",
  }),
);

// How an action reads when it failed, where that differs from its success.
const AUDIT_FAILURE_LABELS: ReadonlyMap<string, string> = new Map(
  Object.entries({ "user.login": "Failed sign-in" }),
);

/**
 * The actions the server records, in the order the filter lists them. These are the exact values
 * the `action` filter matches; the labels above are only how they read.
 */
export const AUDIT_FILTER_ACTIONS: readonly string[] = [
  "org.create",
  "org.update",
  "project.create",
  "project.update",
  "project.delete",
  "key.create",
  "key.revoke",
  "member.add",
  "member.role_change",
  "member.remove",
  "invite.create",
  "invite.accept",
  "invite.revoke",
  "user.password_reset",
  "user.oauth_link",
  "user.oauth_unlink",
  "user.totp_enable",
  "user.totp_disable",
  "credential.create",
  "credential.rotate",
  "credential.delete",
  "gateway_route.create",
  "gateway_route.update",
  "gateway_route.revert",
  "gateway_route.delete",
  "gateway_key.create",
  "gateway_key.update",
  "gateway_key.revoke",
  "fault_profile.create",
  "fault_profile.update",
  "fault_profile.delete",
  "price_override.create",
  "price_override.delete",
  "gateway_cache.purge",
  "alert_channel.create",
  "alert_channel.update",
  "alert_channel.delete",
  "alert_channel.test",
  "alert_channel.retry_delivery",
  "alert_rule.create",
  "alert_rule.update",
  "alert_rule.delete",
  "alert_rule.mute",
  "alert_event.acknowledge",
  "budget.create",
  "budget.update",
  "budget.delete",
  "dataset.lock",
  "dataset.unlock",
  "user.login",
  "user.logout",
  "user.session_revoke",
  "token.create",
  "token.revoke",
  "export.create",
  "audit.export",
  "access.denied",
];

/** The label for a known action, or null so the caller can show the raw action in mono. */
export function auditActionLabel(
  action: string,
  outcome: AuditEvent["outcome"] = "success",
): string | null {
  const failed = outcome === "failure" ? AUDIT_FAILURE_LABELS.get(action) : undefined;
  return failed ?? AUDIT_ACTION_LABELS.get(action) ?? null;
}

const TARGET_TYPE_LABELS: ReadonlyMap<string, string> = new Map(
  Object.entries({
    org: "Organization",
    organization: "Organization",
    project: "Project",
    api_key: "API key",
    key: "API key",
    user: "User",
    member: "Member",
    invite: "Invite",
    session: "Session",
    token: "Access token",
    export: "Export",
    provider_credential: "Provider credential",
    gateway_route: "Gateway route",
    gateway_key: "Gateway key",
    fault_profile: "Fault profile",
    price_override: "Price override",
    alert_channel: "Alert channel",
    alert_rule: "Alert rule",
    alert_event: "Alert",
    budget: "Budget",
    dataset: "Dataset",
  }),
);

export function targetTypeLabel(targetType: string): string {
  return TARGET_TYPE_LABELS.get(targetType) ?? targetType;
}

const SHORT_ID_LENGTH = 8;

/**
 * Eight characters to tell targets apart on screen. Target IDs are UUIDv7, whose leading
 * characters are a timestamp shared by everything created in the same minute, so the short
 * form is taken from the random tail instead. The full ID is shown on hover.
 */
export function shortTargetId(targetId: string): string {
  const compact = targetId.replaceAll("-", "");
  return compact.length > SHORT_ID_LENGTH ? compact.slice(-SHORT_ID_LENGTH) : targetId;
}

function readString(metadata: Record<string, unknown>, key: string): string | null {
  const value = metadata[key];
  return typeof value === "string" && value !== "" ? value : null;
}

// Why an event failed, as the server's `reason` codes read.
const FAILURE_REASONS: ReadonlyMap<string, string> = new Map(
  Object.entries({
    invalid_password: "Wrong password",
    invalid_totp: "Wrong two-factor code",
    two_factor_required: "Two-factor authentication required",
    token_scope: "Read-only access token",
  }),
);

const SIGN_IN_METHODS: ReadonlyMap<string, string> = new Map(
  Object.entries({ password: "with password", totp: "with two-factor code", oauth: "with OAuth" }),
);

/** Why the event failed in words, e.g. "Wrong password" or "Role lacks project:write". */
export function auditFailureReason(
  event: Pick<AuditEvent, "outcome" | "reason"> & { metadata: Record<string, unknown> | null },
): string | null {
  if (event.outcome !== "failure" || !event.reason) {
    return null;
  }
  if (event.reason === "forbidden") {
    const permission = event.metadata ? readString(event.metadata, "permission") : null;
    return permission ? `Role lacks ${permission}` : "Role lacks the permission";
  }
  return FAILURE_REASONS.get(event.reason) ?? event.reason;
}

export interface AuditChange {
  field: string;
  from: unknown;
  to: unknown;
}

function isChange(value: unknown): value is { from: unknown; to: unknown } {
  return typeof value === "object" && value !== null && "from" in value && "to" in value;
}

/**
 * The before and after values the event recorded: `{field: {from, to}}` entries at the top
 * level or under `changes`, and the top-level `from`/`to` pair of a role change. Empty when the
 * event recorded none.
 */
export function auditChanges(metadata: Record<string, unknown> | null): AuditChange[] {
  if (!metadata) {
    return [];
  }
  if ("from" in metadata && "to" in metadata) {
    return [{ field: "role", from: metadata.from, to: metadata.to }];
  }
  const nested = metadata.changes;
  const source =
    typeof nested === "object" && nested !== null ? (nested as Record<string, unknown>) : metadata;
  return Object.entries(source).flatMap(([field, value]) =>
    isChange(value) ? [{ field, from: value.from, to: value.to }] : [],
  );
}

/**
 * A one-line summary of the event for the table: why it failed, the key name, "member → admin",
 * the fields that changed, or how someone signed in. Null when there's nothing worth surfacing.
 */
export function auditEventSummary(
  event: Pick<AuditEvent, "action" | "outcome" | "reason"> & {
    metadata: Record<string, unknown> | null;
  },
): string | null {
  const failure = auditFailureReason(event);
  if (failure) {
    return failure;
  }
  const { action, metadata } = event;
  if (!metadata) {
    return null;
  }
  const from = readString(metadata, "from");
  const to = readString(metadata, "to");
  if (from && to) {
    return `${from} → ${to}`;
  }
  const changes = metadata.changes;
  if (action.startsWith("project.") && changes && typeof changes === "object") {
    const fields = Object.keys(changes);
    return fields.length > 0 ? fields.join(", ") : null;
  }
  const changed = auditChanges(metadata).map((change) => change.field);
  if (changed.length > 0) {
    return changed.join(", ");
  }
  if (action === "user.login") {
    const method = readString(metadata, "method");
    return method ? (SIGN_IN_METHODS.get(method) ?? null) : null;
  }
  return readString(metadata, "name") ?? readString(metadata, "role");
}

export function hasMetadata(metadata: Record<string, unknown> | null): boolean {
  return metadata !== null && Object.keys(metadata).length > 0;
}

/**
 * Who acted: their name, else their email, else the email recorded with the event (a person who
 * has since been deleted), "Admin command" for the server's admin CLI, or "System" for an event
 * the app caused itself.
 */
export function auditActorName(
  event: Pick<AuditEvent, "actor" | "actor_email" | "actor_type">,
): string {
  if (event.actor) {
    return event.actor.name || event.actor.email;
  }
  if (event.actor_email) {
    return event.actor_email;
  }
  return event.actor_type === "cli" ? "Admin command" : "System";
}

/** "via access token" when a person acted through a personal access token, else null. */
export function auditActorVia(event: Pick<AuditEvent, "actor_type">): string | null {
  return event.actor_type === "token" ? "via access token" : null;
}
