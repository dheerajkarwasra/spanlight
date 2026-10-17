import { describe, expect, it } from "vitest";

import { can } from "./permissions";

describe("can", () => {
  it("mirrors the backend role hierarchy", () => {
    expect(can("viewer", "project:read")).toBe(true);
    expect(can("viewer", "key:create")).toBe(false);
    expect(can("member", "key:create")).toBe(true);
    expect(can("member", "key:revoke_any")).toBe(false);
    expect(can("viewer", "insights:feedback")).toBe(false);
    expect(can("member", "insights:feedback")).toBe(true);
    expect(can("member", "insights:manage")).toBe(false);
    expect(can("admin", "member:manage")).toBe(true);
    expect(can("admin", "org:delete")).toBe(false);
    expect(can("owner", "org:delete")).toBe(true);
  });

  it("denies everything without a role", () => {
    expect(can(null, "project:read")).toBe(false);
  });
});
