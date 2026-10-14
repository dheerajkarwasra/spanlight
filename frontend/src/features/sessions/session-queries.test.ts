import { describe, expect, it } from "vitest";

import { SESSION_PAGE_SIZE, toSessionListQuery } from "./session-queries";

const range = { from: "2026-10-13T00:00:00.000Z", to: "2026-10-14T00:00:00.000Z" };

describe("toSessionListQuery", () => {
  it("sends the window, the page size and the selected environment", () => {
    expect(toSessionListQuery(range, "prod")).toEqual({
      from: range.from,
      to: range.to,
      environment: "prod",
      limit: SESSION_PAGE_SIZE,
    });
  });

  it("leaves the environment out for All environments", () => {
    expect(toSessionListQuery(range, undefined).environment).toBeUndefined();
  });
});
