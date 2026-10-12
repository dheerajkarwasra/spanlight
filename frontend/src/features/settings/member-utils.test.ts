import { describe, expect, it } from "vitest";

import { ApiError, type Member, type Role } from "@/lib/api";

import {
  inviteExpiryLabel,
  isRole,
  LAST_OWNER_MESSAGE,
  memberErrorMessage,
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
