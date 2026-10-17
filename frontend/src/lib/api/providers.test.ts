import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setUnauthorizedHandler } from "./client";
import { completenessApi } from "./completeness";
import { insightsApi } from "./insights";
import { providersApi } from "./providers";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

const WINDOW = { from: "2026-10-15T00:00:00Z", to: "2026-10-16T00:00:00Z" };
const ENCODED_WINDOW = "from=2026-10-15T00%3A00%3A00Z&to=2026-10-16T00%3A00%3A00Z";

describe("provider path clients", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setUnauthorizedHandler(vi.fn());
    document.cookie = "spl_csrf=csrf-token-123; path=/";
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse({})));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    document.cookie = "spl_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });

  function lastCall(): { url: string; init: RequestInit | undefined } {
    const call = fetchMock.mock.calls.at(-1);
    if (call === undefined) throw new Error("fetch was not called");
    const [url, init] = call;
    if (typeof url !== "string") throw new Error("fetch was called with a non-string URL");
    return { url, init };
  }

  it("reads provider health over the window, with an optional environment", async () => {
    await providersApi.health("p/1", { ...WINDOW, environment: "prod" });
    expect(lastCall().url).toBe(
      `/api/v1/projects/p%2F1/providers/health?${ENCODED_WINDOW}&environment=prod`,
    );
    expect(lastCall().init?.method).toBe("GET");
  });

  it("reads the error timeseries with its bucket and provider", async () => {
    await providersApi.errorTimeseries("p1", { ...WINDOW, bucket: "hour", provider: "openai" });
    expect(lastCall().url).toBe(
      `/api/v1/projects/p1/providers/errors/timeseries?${ENCODED_WINDOW}&bucket=hour&provider=openai`,
    );
  });

  it("leaves unset sample filters off the query", async () => {
    await providersApi.samples("p1", {
      ...WINDOW,
      provider: "anthropic",
      error_class: "rate_limit",
      rate_limit_kind: "spend_cap",
      environment: undefined,
      limit: 20,
      cursor: null,
    });
    expect(lastCall().url).toBe(
      `/api/v1/projects/p1/providers/errors/samples?${ENCODED_WINDOW}` +
        "&provider=anthropic&error_class=rate_limit&rate_limit_kind=spend_cap&limit=20",
    );
  });

  it("sends the sample cursor encoded", async () => {
    await providersApi.samples("p1", { ...WINDOW, cursor: "a+b/c=" });
    expect(lastCall().url).toBe(
      `/api/v1/projects/p1/providers/errors/samples?${ENCODED_WINDOW}&cursor=a%2Bb%2Fc%3D`,
    );
  });

  it("lists provider incidents and sends api_only=false explicitly", async () => {
    await providersApi.incidents("p1", { ...WINDOW, provider: "google", api_only: false });
    expect(lastCall().url).toBe(
      `/api/v1/projects/p1/provider-incidents?${ENCODED_WINDOW}&provider=google&api_only=false`,
    );
  });

  it("reads telemetry completeness over the window", async () => {
    await completenessApi.get("p1", WINDOW);
    expect(lastCall().url).toBe(`/api/v1/projects/p1/telemetry-completeness?${ENCODED_WINDOW}`);
  });

  it("puts a feedback verdict with the CSRF header", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ verdict: "wrong", note: "flaky", updated_at: "2026-10-16T00:00:00Z" }),
    );
    const answer = await insightsApi.putFeedback("p1", "i/1", { verdict: "wrong", note: "flaky" });
    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/projects/p1/insights/i%2F1/feedback");
    expect(init?.method).toBe("PUT");
    expect(init?.body).toBe(JSON.stringify({ verdict: "wrong", note: "flaky" }));
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("csrf-token-123");
    expect(answer.verdict).toBe("wrong");
  });

  it("deletes the caller's feedback", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(insightsApi.deleteFeedback("p1", "i1")).resolves.toBeUndefined();
    expect(lastCall().url).toBe("/api/v1/projects/p1/insights/i1/feedback");
    expect(lastCall().init?.method).toBe("DELETE");
  });

  it("reads precision for all time without a window, and over one when given", async () => {
    await insightsApi.precision("p1");
    expect(lastCall().url).toBe("/api/v1/projects/p1/insights/precision");
    await insightsApi.precision("p1", WINDOW);
    expect(lastCall().url).toBe(`/api/v1/projects/p1/insights/precision?${ENCODED_WINDOW}`);
  });
});
