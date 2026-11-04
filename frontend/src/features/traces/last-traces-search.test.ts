import { describe, expect, it } from "vitest";

import {
  recallOpenedTrace,
  recallTracesSearch,
  rememberOpenedTrace,
  rememberTracesSearch,
} from "./last-traces-search";

describe("trace history scope", () => {
  it.each([
    ["user-1", "org-1", "project-2"],
    ["user-1", "org-2", "project-1"],
    ["user-2", "org-1", "project-1"],
    ["user-2", "org-2", "project-2"],
  ])("isolates history for identity tuple %s/%s/%s", (user, org, project) => {
    const original = JSON.stringify(["user-1", "org-1", "project-1"]);
    const next = JSON.stringify([user, org, project]);
    rememberOpenedTrace("private-trace", original);
    rememberTracesSearch({ q: "private-filter" }, original);
    expect(recallOpenedTrace(next)).toBeNull();
    expect(recallTracesSearch(next)).toEqual({});
    expect(recallOpenedTrace(original)).toBeNull();
    expect(recallTracesSearch(original)).toEqual({});
  });
  it("does not reuse a selected trace or filter across projects or users", () => {
    rememberOpenedTrace("trace-a", "user-1/project-a");
    rememberTracesSearch({ q: "private", peek: "trace-a" }, "user-1/project-a");
    expect(recallOpenedTrace("user-1/project-a")).toBe("trace-a");
    expect(recallTracesSearch("user-1/project-a")).toEqual({ q: "private" });
    expect(recallOpenedTrace("user-1/project-b")).toBeNull();
    expect(recallTracesSearch("user-1/project-b")).toEqual({});
    expect(recallOpenedTrace("user-2/project-a")).toBeNull();
    expect(recallTracesSearch("user-2/project-a")).toEqual({});
  });
});
