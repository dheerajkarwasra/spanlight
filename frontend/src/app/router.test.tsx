import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, authApi, orgsApi, projectsApi, type Org } from "@/lib/api";
import { makeProject, ME, mockSignedIn, mockSignedOut, ORG_ID, renderApp } from "@/test/render-app";

const GLOBEX: Org = {
  id: "org_2",
  name: "Globex",
  slug: "globex",
  is_demo: false,
  require_2fa: false,
};

const INDEX_HTML_TITLE = "Spanlight — Open-source LLM observability";

async function expectTitle(title: string) {
  await waitFor(() => {
    expect(document.title).toBe(title);
  });
}

describe("document titles", () => {
  beforeEach(() => {
    document.head.innerHTML = `<title>${INDEX_HTML_TITLE}</title>`;
  });

  it("names each page and follows navigation", async () => {
    const user = userEvent.setup();
    mockSignedOut();
    renderApp("/login");
    await expectTitle("Sign in · Spanlight");

    await user.click(screen.getByRole("link", { name: "Create an account" }));
    await expectTitle("Create an account · Spanlight");

    // Routes only manage the title; the meta tags stay index.html's.
    expect(document.head.querySelectorAll("meta")).toHaveLength(0);
  });

  it("names pages inside the app shell", async () => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    renderApp(`/${ORG_ID}/proj_1/settings/project`);
    await expectTitle("Project settings · Spanlight");
  });

  it("names the traces page", async () => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    renderApp(`/${ORG_ID}/proj_1/traces`);
    await expectTitle("Traces · Spanlight");
  });

  it("names the spend page", async () => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    renderApp(`/${ORG_ID}/proj_1/attribution`);
    await expectTitle("Spend · Spanlight");
  });

  it("says when a page doesn't exist", async () => {
    mockSignedOut();
    renderApp("/no/such/page/here");
    expect(await screen.findByText("Page not found")).toBeInTheDocument();
    await expectTitle("Page not found · Spanlight");
  });
});

describe("workspace URLs", () => {
  it("replaces an id link with the slug form, keeping the path and the search", async () => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    renderApp(`/${ORG_ID}/proj_1/traces?range=7d`);
    await waitFor(() => {
      expect(window.location.pathname).toBe("/acme/checkout/traces");
    });
    expect(window.location.search).toBe("?range=7d");
    await expectTitle("Traces · Spanlight");
  });

  it("opens a slug link and hands the ids to the pages", async () => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    renderApp("/acme/checkout/settings/project");
    await expectTitle("Project settings · Spanlight");
    expect(projectsApi.get).toHaveBeenCalledWith("proj_1");
    expect(projectsApi.get).not.toHaveBeenCalledWith("checkout");
    expect(window.location.pathname).toBe("/acme/checkout/settings/project");
  });

  it("builds slug links from ids", async () => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    const router = renderApp("/acme/checkout/settings/project");
    await expectTitle("Project settings · Spanlight");
    const [tracesLink] = await screen.findAllByRole("link", { name: "Traces" });
    expect(tracesLink).toHaveAttribute("href", "/acme/checkout/traces");
    const location = router.buildLocation({
      to: "/$orgId/$projectId/sessions",
      params: { orgId: ORG_ID, projectId: "proj_1" },
    });
    expect(location.href).toBe("/acme/checkout/sessions");
  });

  it("says when a slug names no project", async () => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    renderApp("/acme/no-such-project/traces");
    expect(await screen.findByText("Page not found")).toBeInTheDocument();
    // The router's not-found, not the shell's answer to a project query.
    expect(projectsApi.get).not.toHaveBeenCalled();
  });

  it("keeps the hash, still encoded, when it replaces an id link", async () => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    renderApp(`/${ORG_ID}/proj_1/traces#a%23b`);
    await waitFor(() => {
      expect(window.location.pathname).toBe("/acme/checkout/traces");
    });
    expect(window.location.hash).toBe("#a%23b");
  });

  it("opens projects of a healthy org while another org's project list fails", async () => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(authApi, "me").mockResolvedValue({
      ...ME,
      memberships: [...ME.memberships, { org: { ...GLOBEX, slug: "globex" }, role: "member" }],
    });
    vi.spyOn(orgsApi, "projects").mockImplementation((orgId) =>
      orgId === ORG_ID
        ? Promise.resolve([makeProject("proj_1", "Checkout")])
        : Promise.reject(new ApiError(500, {})),
    );
    renderApp("/acme/checkout/traces");
    await expectTitle("Traces · Spanlight");
    expect(window.location.pathname).toBe("/acme/checkout/traces");
  });
});

describe("workspace URLs of an org that requires two-factor authentication", () => {
  const LOCKED_PROJECT_ID = "5d7f3f7e-2b8f-4d34-8f64-6c1f0e2a0b01";

  function lockOrg() {
    mockSignedIn([makeProject(LOCKED_PROJECT_ID, "Checkout")]);
    const locked = new ApiError(403, { code: "TWO_FACTOR_REQUIRED" });
    vi.spyOn(orgsApi, "projects").mockRejectedValue(locked);
    vi.spyOn(projectsApi, "get").mockRejectedValue(locked);
  }

  it("sends a slug link to the account's Security page", async () => {
    lockOrg();
    renderApp("/acme/checkout/traces");
    await waitFor(() => {
      expect(window.location.pathname).toBe("/account/security");
    });
  });

  it("still shows the turn-on card for an id link", async () => {
    lockOrg();
    renderApp(`/${ORG_ID}/${LOCKED_PROJECT_ID}/traces`);
    expect(
      await screen.findByRole("heading", { name: "Turn on two-factor authentication" }),
    ).toBeInTheDocument();
    expect(window.location.pathname).toBe(`/acme/${LOCKED_PROJECT_ID}/traces`);
  });

  it("opens the slug link once the org's projects can be listed", async () => {
    lockOrg();
    const router = renderApp("/acme/checkout/traces");
    await waitFor(() => {
      expect(window.location.pathname).toBe("/account/security");
    });

    mockSignedIn([makeProject(LOCKED_PROJECT_ID, "Checkout")]);
    await act(() => router.navigate({ href: "/acme/checkout/traces" }));
    await expectTitle("Traces · Spanlight");
    expect(window.location.pathname).toBe("/acme/checkout/traces");
  });
});
