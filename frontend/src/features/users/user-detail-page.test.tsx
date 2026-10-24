import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { endUsersApi, type UserDetail } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const DETAIL: UserDetail = {
  user: {
    external_user_id: "user_8f2c91",
    traces: 128,
    llm_calls: 342,
    errors: 3,
    unpriced_calls: 0,
    cost_usd: "1.92",
    tokens: 412_880,
    first_seen_day: "2026-10-02",
    last_seen_day: "2026-10-21",
  },
  daily: [],
  recent_sessions: [],
  window: { start: "2026-09-22T00:00:00Z", end: "2026-10-22T00:00:00Z" },
  computed_at: null,
};

describe("UserDetailPage", () => {
  beforeEach(() => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(endUsersApi, "get").mockResolvedValue(DETAIL);
  });

  it("links to the user's traces and keeps the time window", async () => {
    renderApp(`/${ORG_ID}/proj_1/users/user_8f2c91?range=7d&env=production`);

    const link = await screen.findByRole("link", { name: "View traces" });
    const href = new URL(link.getAttribute("href") ?? "", "http://localhost");
    expect(href.pathname).toMatch(/\/traces$/);
    expect(href.searchParams.get("user")).toBe("user_8f2c91");
    expect(href.searchParams.get("range")).toBe("7d");
    expect(href.searchParams.get("env")).toBe("production");
  });
});
