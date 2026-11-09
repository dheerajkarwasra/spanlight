import { describe, expect, it } from "vitest";

import { ApiError, type Member, type Role } from "@/lib/api";

import {
  inviteExpiryLabel,
  isRole,
  LAST_OWNER_MESSAGE,
  memberErrorMessage,
  memberRoleDescription,
  memberRemovalKeyLines,
  roleHint,
  roleWithArticle,
  sortMembers,
} from "./member-utils";

function member(name: string, role: Role): Member {
  return {
    user: {
      id: name,
      email: `${name.toLowerCase()}@example.com`,
      name,
      created_at: "",
      email_verified: true,
    },
    role,
    read_keys: 0,
    created_at: "2026-10-01T00:00:00Z",
  };
}

describe("memberErrorMessage", () => {
  it("explains the last-owner conflict", () => {
    const error = new ApiError(409, { code: "LAST_OWNER", detail: "Cannot demote last owner" });
    expect(memberErrorMessage(error)).toBe(LAST_OWNER_MESSAGE);
  });

  it("falls back to the API message", () => {
    const error = new ApiError(403, { detail: "Not allowed to do that." });
    expect(memberErrorMessage(error)).toBe("Not allowed to do that.");
  });
});

it("explains removal for zero, one and multiple read keys", () => {
  expect(memberRemovalKeyLines(0)).toEqual([
    "No API key they created has read access.",
    "Gateway keys they created keep working and spending. Revoke any that should stop under Gateway.",
  ]);
  expect(memberRemovalKeyLines(1)).toEqual([
    "1 API key they created loses read access: read-only keys are revoked, ingest keys keep sending traces.",
    "Gateway keys they created keep working and spending. Revoke any that should stop under Gateway.",
  ]);
  expect(memberRemovalKeyLines(2)).toEqual([
    "2 API keys they created lose read access: read-only keys are revoked, ingest keys keep sending traces.",
    "Gateway keys they created keep working and spending. Revoke any that should stop under Gateway.",
  ]);
});

describe("sortMembers", () => {
  it("orders by role, then name", () => {
    const sorted = sortMembers([
      member("Zoe", "viewer"),
      member("Bob", "admin"),
      member("Amy", "admin"),
      member("Oli", "owner"),
    ]);
    expect(sorted.map((entry) => entry.user.name)).toEqual(["Oli", "Amy", "Bob", "Zoe"]);
  });
});

describe("isRole", () => {
  it("accepts known roles only", () => {
    expect(isRole("admin")).toBe(true);
    expect(isRole("superuser")).toBe(false);
  });
});

describe("inviteExpiryLabel", () => {
  const now = new Date("2026-10-07T12:00:00Z");

  it("describes a future expiry", () => {
    expect(inviteExpiryLabel("2026-10-14T12:00:00Z", now)).toBe("Expires in 7 days");
    expect(inviteExpiryLabel("2026-10-08T11:00:00Z", now)).toBe("Expires in 23 hours");
    expect(inviteExpiryLabel("2026-10-07T13:00:00Z", now)).toBe("Expires in 1 hour");
    expect(inviteExpiryLabel("2026-10-07T12:30:00Z", now)).toBe("Expires in less than an hour");
  });

  it("marks past invites as expired", () => {
    expect(inviteExpiryLabel("2026-10-06T12:00:00Z", now)).toBe("Expired");
  });
});

describe("roleWithArticle", () => {
  it("uses the right article", () => {
    expect(roleWithArticle("admin")).toBe("an admin");
    expect(roleWithArticle("owner")).toBe("an owner");
    expect(roleWithArticle("member")).toBe("a member");
    expect(roleWithArticle("viewer")).toBe("a viewer");
  });
});

describe("roleHint", () => {
  it("leads with the role name", () => {
    expect(roleHint("member")).toBe(
      "Member: create their own API keys, score traces, review queues, and manage prompts, datasets, playground runs and experiments.",
    );
    expect(roleHint("viewer")).toBe("Viewer: read-only access to traces and metrics.");
  });
});

it("describes the new role and the direction of the access change", () => {
  expect(memberRoleDescription("viewer", "admin")).toBe(
    "Admins manage projects, members, all API keys and the audit log. Takes effect at once. Confirm it's you first.",
  );
  expect(memberRoleDescription("owner", "member")).toBe(
    "Members create their own API keys and manage prompts, datasets, scores and experiments. No member or project management. They lose their current access at once. Confirm it's you first.",
  );
  expect(memberRoleDescription("member", "owner")).toBe(
    "Owners get full access, including deleting the organization. Takes effect at once. Confirm it's you first.",
  );
  expect(memberRoleDescription("admin", "viewer")).toBe(
    "Viewers only read traces and metrics. They lose their current access at once. Confirm it's you first.",
  );
});
