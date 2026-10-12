import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authApi } from "./auth";
import { api, setUnauthorizedHandler } from "./client";
import { ApiError } from "./errors";
import type { User } from "./types";

const USER: User = {
  id: "u1",
  email: "ada@example.com",
  name: "Ada",
  created_at: "2026-10-08T12:00:00Z",
  email_verified: true,
};
const CREDENTIALS = { email: "ada@example.com", password: "correct horse battery" };

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

describe("authApi.login", () => {
  const fetchMock = vi.fn<typeof fetch>();
  const onUnauthorized = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setUnauthorizedHandler(onUnauthorized);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    onUnauthorized.mockReset();
  });

  it("resolves with a signed_in outcome carrying the user when the password is enough", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: "signed_in", user: USER }));

    await expect(authApi.login(CREDENTIALS)).resolves.toEqual({ status: "signed_in", user: USER });

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/v1/auth/login");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify(CREDENTIALS));
  });

  it("keeps rejecting a wrong password with the server's problem", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { title: "Unauthorized", status: 401, code: "INVALID_CREDENTIALS", detail: "Nope." },
        { status: 401 },
      ),
    );

    const error = await authApi.login(CREDENTIALS).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("INVALID_CREDENTIALS");
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});

describe("the second step of signing in", () => {
  const fetchMock = vi.fn<typeof fetch>();
  const onUnauthorized = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setUnauthorizedHandler(onUnauthorized);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    onUnauthorized.mockReset();
  });

  it("does not treat a wrong code as an expired session", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { title: "Unauthorized", status: 401, code: "INVALID_TOTP_CODE" },
        { status: 401 },
      ),
    );

    const error = await api
      .post("/api/v1/auth/totp/verify", { challenge: "c", code: "000000" })
      .catch((caught: unknown) => caught);

    expect((error as ApiError).code).toBe("INVALID_TOTP_CODE");
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});
