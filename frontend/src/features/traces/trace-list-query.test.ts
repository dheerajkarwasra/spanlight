import { describe, expect, it } from "vitest";

import { toTraceListQuery } from "./trace-queries";

const WINDOW = { from: "2026-10-01T00:00:00Z", to: "2026-10-02T00:00:00Z" };

describe("toTraceListQuery sort, order and previews", () => {
  it("passes the sort and direction, and asks for previews only when told to", () => {
    const query = toTraceListQuery({ sort: "cost", order: "asc" }, WINDOW, undefined, true);
    expect(query).toMatchObject({ sort: "cost", order: "asc", preview: true });
    expect(toTraceListQuery({}, WINDOW, undefined).preview).toBeUndefined();
  });

  it("leaves the peeked trace out, so opening or stepping the peek never refetches the list", () => {
    expect(toTraceListQuery({ peek: "t1" }, WINDOW, undefined)).toEqual(
      toTraceListQuery({}, WINDOW, undefined),
    );
  });
});
