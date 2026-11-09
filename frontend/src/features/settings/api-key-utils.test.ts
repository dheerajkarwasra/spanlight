import { describe, expect, it } from "vitest";

import type { ApiKey, User } from "@/lib/api";
import { envLine } from "@/lib/reveal-copy";

import { formatKeyPrefix, hasReadKeyScope, revokeDecision, sortApiKeys } from "./api-key-utils";

it("requires owner proof only for scopes that read project data", () => {
  expect(hasReadKeyScope([])).toBe(false);
  expect(hasReadKeyScope(["ingest:write", "scores:write"])).toBe(false);
  expect(hasReadKeyScope(["traces:read"])).toBe(true);
  expect(hasReadKeyScope(["prompts:read", "scores:write"])).toBe(true);
});

const alice: User = {
  id: "u1",
  email: "alice@example.com",
  name: "Alice",
  created_at: "",
  email_verified: true,
};

function makeKey(overrides: Partial<ApiKey>): ApiKey {
  return {
    id: "k",
    name: "Key",
    prefix: "ABCDEFGHIJKL",
    scopes: ["ingest:write"],
    created_by: alice,
    created_at: "2026-10-01T00:00:00Z",
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
    ...overrides,
  };
}

describe("formatKeyPrefix", () => {
  it("adds the scheme when the API returns the bare prefix", () => {
    expect(formatKeyPrefix("ABCDEFGHIJKL")).toBe("spl_live_ABCDEFGHIJKL…");
  });

  it("does not duplicate the scheme", () => {
    expect(formatKeyPrefix("spl_live_ABCD")).toBe("spl_live_ABCD…");
  });
});

describe("sortApiKeys", () => {
  it("lists active keys newest first, then revoked keys", () => {
    const keys = [
      makeKey({ id: "old", created_at: "2026-09-01T00:00:00Z" }),
      makeKey({
        id: "revoked",
        created_at: "2026-10-05T00:00:00Z",
        revoked_at: "2026-10-06T00:00:00Z",
      }),
      makeKey({ id: "new", created_at: "2026-10-02T00:00:00Z" }),
    ];
    expect(sortApiKeys(keys).map((key) => key.id)).toEqual(["new", "old", "revoked"]);
  });
});

describe("revokeDecision", () => {
  const own = makeKey({ created_by: alice });
  const someoneElses = makeKey({ created_by: { ...alice, id: "u2" } });
  const orphaned = makeKey({ created_by: null });

  it("lets admins revoke any key", () => {
    const ability = { canRevokeAny: true, canRevokeOwn: true, userId: "u1" };
    expect(revokeDecision(someoneElses, ability).allowed).toBe(true);
    expect(revokeDecision(orphaned, ability).allowed).toBe(true);
  });

  it("lets members revoke only their own keys", () => {
    const ability = { canRevokeAny: false, canRevokeOwn: true, userId: "u1" };
    expect(revokeDecision(own, ability).allowed).toBe(true);
    expect(revokeDecision(someoneElses, ability)).toEqual({
      allowed: false,
      reason: "You can only revoke keys you created.",
    });
    expect(revokeDecision(orphaned, ability).allowed).toBe(false);
  });

  it("does not let viewers revoke keys", () => {
    const ability = { canRevokeAny: false, canRevokeOwn: false, userId: "u1" };
    expect(revokeDecision(own, ability).allowed).toBe(false);
  });
});

describe("envLine", () => {
  it("formats the environment variable", () => {
    expect(envLine("spl_live_A_B")).toBe("SPANLIGHT_API_KEY=spl_live_A_B");
  });
});
