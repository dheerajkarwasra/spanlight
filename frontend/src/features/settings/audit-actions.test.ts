import { describe, expect, it } from "vitest";

import {
  auditActionLabel,
  auditEventSummary,
  hasMetadata,
  shortTargetId,
  targetTypeLabel,
} from "./audit-actions";

describe("auditActionLabel", () => {
  it.each([
    ["key.create", "Created API key"],
    ["key.created", "Created API key"],
    ["key.revoke", "Revoked API key"],
    ["member.role_change", "Changed member role"],
    ["member.role_changed", "Changed member role"],
    ["invite.accept", "Accepted invite"],
    ["project.update", "Updated project settings"],
    ["user.password_reset", "Reset password"],
    ["user.login", "Signed in"],
  ])("labels %s", (action, label) => {
    expect(auditActionLabel(action)).toBe(label);
  });

  it("returns null for unknown actions so the raw string can be shown", () => {
    expect(auditActionLabel("billing.plan_change")).toBeNull();
  });

  it("does not match inherited object keys", () => {
    expect(auditActionLabel("toString")).toBeNull();
    expect(auditActionLabel("constructor")).toBeNull();
  });
});

describe("targetTypeLabel", () => {
  it("humanises known target types and keeps unknown ones", () => {
    expect(targetTypeLabel("api_key")).toBe("API key");
    expect(targetTypeLabel("org")).toBe("Organization");
    expect(targetTypeLabel("webhook")).toBe("webhook");
  });
});

describe("shortTargetId", () => {
  it("uses the random tail of a UUIDv7, not its shared timestamp prefix", () => {
    expect(shortTargetId("01a118f5-21af-7cfe-9030-b48ff223c136")).toBe("f223c136");
    expect(shortTargetId("01a118f5-2299-7914-9e98-3360e189ba9a")).toBe("e189ba9a");
  });

  it("keeps short IDs as they are", () => {
    expect(shortTargetId("key_1")).toBe("key_1");
  });
});

describe("auditEventSummary", () => {
  // A successful event with these fields; `null` stands for an event the server sent no object for.
  const summary = (action: string, metadata: Record<string, unknown> | null) =>
    auditEventSummary({
      action,
      metadata,
      outcome: "success",
      reason: null,
    });

  it("summarises role changes", () => {
    expect(summary("member.role_change", { from: "member", to: "admin" })).toBe("member → admin");
  });

  it("lists changed project fields", () => {
    expect(summary("project.update", { changes: { retention_days: 7, name: "Bot" } })).toBe(
      "retention_days, name",
    );
  });

  it("uses the name, then the role", () => {
    expect(summary("key.create", { name: "production-api", prefix: "idk" })).toBe("production-api");
    expect(summary("invite.create", { role: "viewer" })).toBe("viewer");
  });

  it("returns null when there is nothing to show", () => {
    expect(summary("key.create", null)).toBeNull();
    expect(summary("key.create", {})).toBeNull();
  });
});

describe("hasMetadata", () => {
  it("is false for null and empty objects", () => {
    expect(hasMetadata(null)).toBe(false);
    expect(hasMetadata({})).toBe(false);
    expect(hasMetadata({ name: "x" })).toBe(true);
  });
});
