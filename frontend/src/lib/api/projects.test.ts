import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setUnauthorizedHandler } from "./client";
import { projectsApi } from "./projects";

describe("projectsApi.sessionTurns", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setUnauthorizedHandler(vi.fn());
    fetchMock.mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ items: [], next_cursor: null }), {
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  function lastUrl(): string {
    const url = fetchMock.mock.calls.at(-1)?.[0];
    if (typeof url !== "string") throw new Error("fetch was not called with a string URL");
    return url;
  }

  it.each([
    ["a/b", "a%2Fb"],
    ["..", "~.."],
    [".", "~."],
    ["~x", "~~x"],
    ["chat 1", "chat%201"],
  ])("sends session id %s as one path segment (%s)", async (sessionId, segment) => {
    await projectsApi.sessionTurns("proj_1", sessionId, { limit: 100, cursor: "c1" });
    expect(lastUrl()).toBe(`/api/v1/projects/proj_1/sessions/${segment}/turns?limit=100&cursor=c1`);
  });
});
