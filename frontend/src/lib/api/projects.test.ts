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

describe("projectsApi.activation", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads the project's activation steps", async () => {
    const fetchMock = vi.fn<typeof fetch>(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({ first_trace: true, session: false, budget: false, evaluation: false }),
          { headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    setUnauthorizedHandler(vi.fn());
    await expect(projectsApi.activation("proj_1")).resolves.toMatchObject({ first_trace: true });
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe("/api/v1/projects/proj_1/activation");
  });
});
