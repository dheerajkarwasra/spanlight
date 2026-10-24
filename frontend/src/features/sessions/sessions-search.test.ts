import { describe, expect, it } from "vitest";

import { sessionsSearchSchema } from "./sessions-search";

describe("sessionsSearchSchema", () => {
  it("leaves the default sort, direction and search absent", () => {
    const search = sessionsSearchSchema.parse({});
    expect([search.sort, search.order, search.q]).toEqual([undefined, undefined, undefined]);
  });

  it("keeps every list sort with a direction and a search", () => {
    expect(sessionsSearchSchema.parse({ sort: "tokens", order: "asc", q: "chat_" })).toMatchObject({
      sort: "tokens",
      order: "asc",
      q: "chat_",
    });
  });

  it("falls back on an unknown sort or direction and drops an empty or oversized search", () => {
    const search = sessionsSearchSchema.parse({ sort: "sideways", order: "up", q: "" });
    expect([search.sort, search.order, search.q]).toEqual([undefined, undefined, undefined]);
    expect(sessionsSearchSchema.parse({ q: "x".repeat(257) }).q).toBeUndefined();
  });

  it("keeps the shared window params", () => {
    expect(sessionsSearchSchema.parse({ range: "7d", env: "prod" })).toMatchObject({
      range: "7d",
      env: "prod",
    });
  });
});
