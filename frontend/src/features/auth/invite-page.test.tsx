import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, authApi, type InvitePreview } from "@/lib/api";
import { mockSignedIn, mockSignedOut, renderApp } from "@/test/render-app";

const PREVIEW: InvitePreview = {
  org: { id: "org_2", name: "Globex", slug: "globex" },
  role: "member",
  expires_at: "2026-11-08T00:00:00Z",
};

function mockInvite() {
  vi.spyOn(authApi, "previewInvite").mockResolvedValue(PREVIEW);
  vi.spyOn(authApi, "acceptInvite").mockResolvedValue({
    org: { ...PREVIEW.org, is_demo: false, require_2fa: false },
    role: "member",
  });
}

const INVITED = { name: "You're invited to join Globex" };

beforeEach(() => {
  window.sessionStorage.clear();
});

describe("invite page", () => {
  it("previews and accepts the token from the fragment, then clears it from the address bar", async () => {
    const user = userEvent.setup();
    mockSignedIn([]);
    mockInvite();
    renderApp("/invite#token=abc");

    expect(await screen.findByRole("heading", INVITED)).toBeInTheDocument();
    expect(authApi.previewInvite).toHaveBeenCalledWith("abc");
    expect(window.location.pathname).toBe("/invite");
    expect(window.location.hash).toBe("");

    await user.click(screen.getByRole("button", { name: "Accept invite" }));
    await waitFor(() => {
      expect(authApi.acceptInvite).toHaveBeenCalledWith("abc");
    });
  });

  it("replaces an old /invite/<token> link with the fragment form, and accepts it", async () => {
    const user = userEvent.setup();
    mockSignedIn([]);
    mockInvite();
    const historyLength = window.history.length;
    renderApp("/invite/abc");

    expect(await screen.findByRole("heading", INVITED)).toBeInTheDocument();
    expect(authApi.previewInvite).toHaveBeenCalledWith("abc");
    expect(window.location.pathname).toBe("/invite");
    expect(window.location.hash).toBe("");
    expect(window.history.length).toBe(historyLength);

    await user.click(screen.getByRole("button", { name: "Accept invite" }));
    await waitFor(() => {
      expect(authApi.acceptInvite).toHaveBeenCalledWith("abc");
    });
  });

  it("keeps nothing in storage for a signed-in visitor", async () => {
    mockSignedIn([]);
    mockInvite();
    renderApp("/invite#token=abc");

    expect(await screen.findByRole("heading", INVITED)).toBeInTheDocument();
    expect(window.sessionStorage.length).toBe(0);
  });

  it("keeps the token out of the sign-in links, and still has it after signing in", async () => {
    mockSignedOut();
    renderApp("/invite#token=abc");

    const signIn = await screen.findByRole("link", { name: "Sign in" });
    expect(signIn).toHaveAttribute("href", "/login?next=%2Finvite");
    expect(screen.getByRole("link", { name: "Create an account" })).toHaveAttribute(
      "href",
      "/signup?next=%2Finvite",
    );
    expect(window.location.hash).toBe("");

    // Signing in (or an OAuth round trip) comes back to /invite in the same tab.
    cleanup();
    vi.restoreAllMocks();
    mockSignedIn([]);
    mockInvite();
    renderApp("/invite");

    expect(await screen.findByRole("heading", INVITED)).toBeInTheDocument();
    expect(authApi.previewInvite).toHaveBeenCalledWith("abc");
  });

  it("keeps the token for the next account when switching accounts", async () => {
    const user = userEvent.setup();
    mockSignedIn([]);
    mockInvite();
    vi.spyOn(authApi, "logout").mockImplementation(() => {
      mockSignedOut();
      return Promise.resolve();
    });
    renderApp("/invite#token=abc");

    await user.click(
      await screen.findByRole("button", { name: "Sign in with a different account" }),
    );
    await waitFor(() => {
      expect(window.location.pathname).toBe("/login");
    });
    expect(window.location.search).toBe("?next=%2Finvite");
    expect(window.sessionStorage.length).toBe(1);
  });

  it("forgets a stashed token the server refuses to preview", async () => {
    mockSignedOut();
    renderApp("/invite#token=abc");
    await screen.findByRole("link", { name: "Sign in" });
    expect(window.sessionStorage.length).toBe(1);

    cleanup();
    vi.restoreAllMocks();
    mockSignedIn([]);
    vi.spyOn(authApi, "previewInvite").mockRejectedValue(new ApiError(404, {}));
    renderApp("/invite");

    expect(
      await screen.findByRole("heading", { name: "This invite can't be used" }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(window.sessionStorage.length).toBe(0);
    });
  });

  it.each([
    [409, 0],
    [410, 0],
    [500, 1],
  ])("after an accept that fails with %i, keeps %i stashed tokens", async (status, kept) => {
    const user = userEvent.setup();
    mockSignedOut();
    renderApp("/invite#token=abc");
    await screen.findByRole("link", { name: "Sign in" });

    cleanup();
    vi.restoreAllMocks();
    mockSignedIn([]);
    mockInvite();
    vi.mocked(authApi.acceptInvite).mockRejectedValue(new ApiError(status, {}));
    renderApp("/invite");

    await user.click(await screen.findByRole("button", { name: "Accept invite" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(window.sessionStorage.length).toBe(kept);
  });

  it("says the link can't be used when it carries no token", async () => {
    mockSignedIn([]);
    mockInvite();
    renderApp("/invite");

    expect(
      await screen.findByRole("heading", { name: "This invite can't be used" }),
    ).toBeInTheDocument();
    expect(authApi.previewInvite).not.toHaveBeenCalled();
  });
});
