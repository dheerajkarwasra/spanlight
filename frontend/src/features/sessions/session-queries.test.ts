import { describe, expect, it } from "vitest";

import { SESSION_PAGE_SIZE, toSessionListQuery } from "./session-queries";

const range = { from: "2026-10-13T00:00:00.000Z", to: "2026-10-14T00:00:00.000Z" };
const list = { sort: "last", order: "desc", q: undefined } as const;

describe("toSessionListQuery", () => {
  it("sends the window, the page size, the list state and the selected environment", () => {
    expect(
      toSessionListQuery({ sort: "cost", order: "asc", q: "chat" }, range, "prod", true),
    ).toEqual({
      from: range.from,
      to: range.to,
      environment: "prod",
      q: "chat",
      sort: "cost",
      order: "asc",
      preview: true,
      limit: SESSION_PAGE_SIZE,
    });
  });

  it("leaves the environment and previews out when not needed", () => {
    const query = toSessionListQuery(list, range, undefined, false);
    expect(query.environment).toBeUndefined();
    expect(query.preview).toBeUndefined();
  });
});
