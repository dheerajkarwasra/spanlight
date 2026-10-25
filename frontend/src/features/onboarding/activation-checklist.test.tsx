import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { authApi, insightsApi, projectsApi, type Activation, type Me } from "@/lib/api";
import { makeProject, ME, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

vi.mock("@/features/alerts/alerts-page", () => ({
  AlertsPage: () => <p>Alerts page</p>,
}));

const HALF: Activation = { first_trace: true, session: true, budget: false, evaluation: false };
const ALL: Activation = { first_trace: true, session: true, budget: true, evaluation: true };

/** A window at least 1280 px wide (Tailwind `xl`), where the full rail shows. */
function wideWindow() {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: query === "(min-width: 80rem)",
        media: query,
        onchange: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

function signIn({
  dismissed = false,
  role = "owner",
  isDemo = false,
  activation = HALF,
}: {
  dismissed?: boolean;
  role?: Me["memberships"][number]["role"];
  isDemo?: boolean;
  activation?: Activation;
} = {}) {
  mockSignedIn([makeProject("proj_1", "Checkout")]);
  vi.spyOn(authApi, "me").mockResolvedValue({
    ...ME,
    activation_checklist_dismissed: dismissed,
    memberships: ME.memberships.map((membership) => ({
      org: { ...membership.org, is_demo: isDemo },
      role,
    })),
  });
  vi.spyOn(projectsApi, "onboarding").mockResolvedValue({
    has_traces: true,
    first_trace_at: "2026-10-01T00:00:00Z",
  });
  vi.spyOn(projectsApi, "activation").mockResolvedValue(activation);
  vi.spyOn(insightsApi, "summary").mockResolvedValue({
    open_critical: 0,
    open_warning: 0,
    open_info: 0,
  });
  return vi.spyOn(authApi, "setActivationChecklist").mockResolvedValue(undefined);
}

async function railOf() {
  await screen.findByText("Alerts page");
  const nav = await screen.findByRole("navigation", { name: "Main" });
  return nav.closest("aside") as HTMLElement;
}

describe("ActivationChecklist on the full rail", () => {
  beforeEach(() => {
    window.localStorage.clear();
    wideWindow();
  });

  it("shows the tile with its count and opens nothing until clicked", async () => {
    signIn();
    renderApp(`/${ORG_ID}/proj_1/alerts`);
    const rail = await railOf();

    const tile = await within(rail).findByRole("button", { name: /Get set up/ });
    expect(tile).toHaveTextContent("2 of 4");
    expect(tile).toHaveAttribute("aria-haspopup", "dialog");
    expect(tile).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(projectsApi.activation).toHaveBeenCalledTimes(1);

    await userEvent.click(tile);
    const dialog = await screen.findByRole("dialog", { name: "Get set up" });
    expect(tile).toHaveAttribute("aria-expanded", "true");
    expect(dialog).toHaveTextContent("2 of 4 done");
    // Opening reads the steps again, so a step finished in another tab shows as done.
    expect(projectsApi.activation).toHaveBeenCalledTimes(2);
  });

  it("marks done steps with Done text, not buttons, and expands an undone step's guide", async () => {
    signIn();
    renderApp(`/${ORG_ID}/proj_1/alerts`);
    const rail = await railOf();
    await userEvent.click(await within(rail).findByRole("button", { name: /Get set up/ }));
    const dialog = await screen.findByRole("dialog", { name: "Get set up" });

    expect(within(dialog).getAllByText("Done")).toHaveLength(2);
    expect(within(dialog).queryByRole("button", { name: /Send a trace from your app/ })).toBeNull();
    expect(within(dialog).getByText("Send a trace from your app")).toBeInTheDocument();

    const budget = within(dialog).getByRole("button", { name: "Set a budget" });
    expect(budget).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(budget);
    expect(budget).toHaveAttribute("aria-expanded", "true");
    const guide = document.getElementById(budget.getAttribute("aria-controls") ?? "");
    expect(guide).toHaveTextContent(
      "Choose a monthly limit for this project. Spanlight warns you before spend reaches it.",
    );
    expect(
      within(guide as HTMLElement).getByRole("link", { name: "Open Budgets" }),
    ).toHaveAttribute(
      "href",
      // Links show the org and project slugs.
      "/acme/checkout/budgets",
    );
  });

  it("asks a member to find an admin instead of linking to Budgets", async () => {
    signIn({ role: "member" });
    renderApp(`/${ORG_ID}/proj_1/alerts`);
    const rail = await railOf();
    await userEvent.click(await within(rail).findByRole("button", { name: /Get set up/ }));
    const dialog = await screen.findByRole("dialog", { name: "Get set up" });

    await userEvent.click(within(dialog).getByRole("button", { name: "Set a budget" }));
    expect(within(dialog).getByText("Ask an admin to set one.")).toBeInTheDocument();
    expect(within(dialog).queryByRole("link", { name: "Open Budgets" })).toBeNull();
  });

  it("hides on Hide checklist and moves focus to the account menu", async () => {
    const setChecklist = signIn();
    renderApp(`/${ORG_ID}/proj_1/alerts`);
    const rail = await railOf();
    await userEvent.click(await within(rail).findByRole("button", { name: /Get set up/ }));
    const dialog = await screen.findByRole("dialog", { name: "Get set up" });

    await userEvent.click(within(dialog).getByRole("button", { name: "Hide checklist" }));

    expect(setChecklist).toHaveBeenCalledWith(true);
    await waitFor(() => {
      expect(within(rail).queryByRole("button", { name: /Get set up/ })).toBeNull();
    });
    await waitFor(() => {
      expect(within(rail).getByRole("button", { name: /Account menu/ })).toHaveFocus();
    });
  });

  it("moves focus to the account menu when the read on open finds every step done", async () => {
    signIn();
    vi.mocked(projectsApi.activation).mockResolvedValueOnce(HALF).mockResolvedValue(ALL);
    renderApp(`/${ORG_ID}/proj_1/alerts`);
    const rail = await railOf();

    await userEvent.click(await within(rail).findByRole("button", { name: /Get set up/ }));

    await waitFor(() => {
      expect(within(rail).queryByRole("button", { name: /Get set up/ })).toBeNull();
    });
    await waitFor(() => {
      expect(within(rail).getByRole("button", { name: /Account menu/ })).toHaveFocus();
    });
  });

  it("never shows in the demo org and does not ask for the steps", async () => {
    signIn({ isDemo: true });
    renderApp(`/${ORG_ID}/proj_1/alerts`);
    const rail = await railOf();
    await waitFor(() => {
      expect(projectsApi.onboarding).toHaveBeenCalled();
    });

    expect(within(rail).queryByRole("button", { name: /Get set up/ })).toBeNull();
    expect(projectsApi.activation).not.toHaveBeenCalled();
    await userEvent.click(within(rail).getByRole("button", { name: /Account menu/ }));
    await screen.findByRole("menuitem", { name: "Connect your app" });
    expect(screen.queryByRole("menuitem", { name: "Show setup checklist" })).toBeNull();
  });

  it("brings a dismissed checklist back from the account menu", async () => {
    const setChecklist = signIn({ dismissed: true });
    renderApp(`/${ORG_ID}/proj_1/alerts`);
    const rail = await railOf();

    expect(within(rail).queryByRole("button", { name: /Get set up/ })).toBeNull();
    expect(projectsApi.activation).not.toHaveBeenCalled();

    await userEvent.click(within(rail).getByRole("button", { name: /Account menu/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Show setup checklist" }));

    expect(setChecklist).toHaveBeenCalledWith(false);
    expect(await within(rail).findByRole("button", { name: /Get set up/ })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("offers no restore once every step is done", async () => {
    signIn({ dismissed: true, activation: ALL });
    renderApp(`/${ORG_ID}/proj_1/alerts`);
    const rail = await railOf();

    await userEvent.click(within(rail).getByRole("button", { name: /Account menu/ }));
    await screen.findByRole("menuitem", { name: "Connect your app" });
    await waitFor(() => {
      expect(projectsApi.activation).toHaveBeenCalled();
    });
    expect(screen.queryByRole("menuitem", { name: "Show setup checklist" })).toBeNull();
  });

  it("offers no restore while the checklist is showing", async () => {
    signIn();
    renderApp(`/${ORG_ID}/proj_1/alerts`);
    const rail = await railOf();
    await within(rail).findByRole("button", { name: /Get set up/ });

    await userEvent.click(within(rail).getByRole("button", { name: /Account menu/ }));
    await screen.findByRole("menuitem", { name: "Connect your app" });
    expect(screen.queryByRole("menuitem", { name: "Show setup checklist" })).toBeNull();
  });
});

// The test setup's `matchMedia` matches nothing, so the desktop aside is the 1024–1279 icon rail.
describe("ActivationChecklist on the icon rail", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("shows a count tile named for screen readers that opens the same popover", async () => {
    signIn();
    renderApp(`/${ORG_ID}/proj_1/alerts`);
    const rail = await railOf();

    const tile = await within(rail).findByRole("button", { name: "Get set up, 2 of 4 done" });
    expect(tile).toHaveTextContent("2/4");
    await userEvent.click(tile);
    expect(await screen.findByRole("dialog", { name: "Get set up" })).toBeInTheDocument();
  });
});
