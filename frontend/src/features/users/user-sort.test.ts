import { describe, expect, it } from "vitest";

import { sortPhrase, toUserListQuery, USER_PAGE_SIZE } from "./user-sort";

const range = { from: "2026-10-13T00:00:00.000Z", to: "2026-10-14T00:00:00.000Z" };

describe("toUserListQuery", () => {
  it("sends the window, the sort, the direction, the search, the page size and the environment", () => {
    expect(toUserListQuery({ sort: "cost", order: "asc", q: "ada" }, range, "production")).toEqual({
      from: range.from,
      to: range.to,
      sort: "cost",
      order: "asc",
      q: "ada",
      environment: "production",
      limit: USER_PAGE_SIZE,
    });
  });

  it("leaves the environment and the search out when unset", () => {
    const query = toUserListQuery({ sort: "traces", order: "desc" }, range, undefined);
    expect(query.environment).toBeUndefined();
    expect(query.q).toBeUndefined();
  });
});

describe("sortPhrase", () => {
  it("names the direction and keeps unpriced users last for cost", () => {
    expect(sortPhrase("cost", "desc")).toBe("sorted by cost, highest first, unpriced last");
    expect(sortPhrase("cost", "asc")).toBe("sorted by cost, lowest first, unpriced last");
    expect(sortPhrase("errors", "asc")).toBe("sorted by errors, lowest first");
  });
});
