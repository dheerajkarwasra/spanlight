import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authApi } from "./auth";
import { API_PREFIX, api, buildUrl, setUnauthorizedHandler } from "./client";
import { ApiError, NetworkError } from "./errors";
import { orgsApi } from "./orgs";
import { projectsApi } from "./projects";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

describe("api client", () => {
  const fetchMock = vi.fn<typeof fetch>();
  const onUnauthorized = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setUnauthorizedHandler(onUnauthorized);
    document.cookie = "spl_csrf=csrf-token-123; path=/";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    fetchMock.mockReset();
    onUnauthorized.mockReset();
    document.cookie = "spl_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });

  it("omits empty query params", () => {
    expect(buildUrl("/api/v1/x", { a: "1", b: undefined, c: null, d: "", e: 0 })).toBe(
      "/api/v1/x?a=1&e=0",
    );
  });

  it("sends same-origin credentials and no CSRF header on GET", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));

    await api.get("/api/v1/projects/p1", { limit: 50 });

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/v1/projects/p1?limit=50");
    expect(init?.credentials).toBe("same-origin");
    expect(new Headers(init?.headers).has("X-CSRF-Token")).toBe(false);
  });

  it("sends the CSRF cookie value as a header on mutations", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: "k1" }, { status: 201 }));

    await api.post("/api/v1/projects/p1/keys", { name: "ci" });

    const init = fetchMock.mock.calls[0]?.[1];
    const headers = new Headers(init?.headers);
    expect(headers.get("X-CSRF-Token")).toBe("csrf-token-123");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(init?.body).toBe(JSON.stringify({ name: "ci" }));
  });

  it("prefers the __Host- CSRF cookie that https deployments set", async () => {
    // jsdom serves http, where a browser refuses __Host- cookies, so the jar is stubbed.
    vi.spyOn(document, "cookie", "get").mockReturnValue(
      "spl_csrf=planted-by-a-subdomain; __Host-spl_csrf=host-token",
    );
    fetchMock.mockResolvedValue(jsonResponse({ id: "k1" }, { status: 201 }));

    await api.post("/api/v1/projects/p1/keys", { name: "ci" });

    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("X-CSRF-Token")).toBe("host-token");
  });

  it("returns undefined for 204 responses", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.delete("/api/v1/projects/p1/keys/k1")).resolves.toBeUndefined();
  });

  it("parses problem+json into an ApiError", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          type: "about:blank",
          title: "Conflict",
          status: 409,
          detail: "Cannot remove the last owner",
          code: "LAST_OWNER",
          request_id: "req_1",
          errors: [{ field: "role", message: "Invalid" }],
        },
        { status: 409, headers: { "Content-Type": "application/problem+json" } },
      ),
    );

    const error = await api.delete("/api/v1/orgs/o1/members/u1").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(409);
    expect(apiError.code).toBe("LAST_OWNER");
    expect(apiError.message).toBe("Cannot remove the last owner");
    expect(apiError.requestId).toBe("req_1");
    expect(apiError.fieldErrors).toEqual([{ field: "role", message: "Invalid" }]);
  });

  it("falls back to the status code for non-JSON errors", async () => {
    fetchMock.mockResolvedValue(new Response("<html>Bad gateway</html>", { status: 502 }));

    const error = await api.get("/api/v1/x").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("HTTP_502");
    expect((error as ApiError).title).toBe("Server error");
  });

  it("calls the unauthorized handler on 401, except for /api/v1/auth/me", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(jsonResponse({ title: "Unauthorized", status: 401 }, { status: 401 })),
    );

    await api.get("/api/v1/auth/me").catch(() => undefined);
    expect(onUnauthorized).not.toHaveBeenCalled();

    await api.get("/api/v1/projects/p1").catch(() => undefined);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("wraps transport failures in a NetworkError", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(api.get("/api/v1/x")).rejects.toBeInstanceOf(NetworkError);
  });

  it("serves the dashboard API from the versioned /api/v1 prefix", () => {
    expect(API_PREFIX).toBe("/api/v1");
  });

  it("exempts the unauthenticated auth endpoints from the 401 redirect", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(jsonResponse({ title: "Unauthorized", status: 401 }, { status: 401 })),
    );

    await authApi.me().catch(() => undefined);
    await authApi.login({ email: "a@example.com", password: "pw" }).catch(() => undefined);
    await authApi
      .signup({ email: "a@example.com", password: "pw", name: "Ada" })
      .catch(() => undefined);
    expect(onUnauthorized).not.toHaveBeenCalled();

    await authApi.sessions().catch(() => undefined);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("sends every domain client request to /api/v1", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse({})));

    await authApi.me();
    await authApi.demoSession();
    await authApi.previewInvite("tok");
    await orgsApi.create("Acme");
    await orgsApi.projects("o1");
    await projectsApi.get("p1");
    await projectsApi.keys("p1");

    const urls = fetchMock.mock.calls.map(([url]) => {
      expect(typeof url).toBe("string");
      return url as string;
    });
    expect(urls).toHaveLength(7);
    for (const url of urls) {
      expect(url.startsWith("/api/v1/")).toBe(true);
    }
    expect(urls).toContain("/api/v1/auth/me");
    expect(urls).toContain("/api/v1/orgs/o1/projects");
    expect(urls).toContain("/api/v1/projects/p1/keys");
  });
});
