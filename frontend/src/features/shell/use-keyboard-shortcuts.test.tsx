import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const lock = vi.hoisted(() => ({ locked: false }));

vi.mock("@/lib/navigation-lock", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  isNavigationLocked: () => lock.locked,
}));

vi.mock("@/features/attribution/attribution-page", () => ({
  AttributionPage: () => <p>Spend page</p>,
}));

const START = `/${ORG_ID}/proj_1/settings/project`;

async function renderShell() {
  const router = renderApp(START);
  await screen.findByRole("navigation", { name: "Main" });
  return router;
}

describe("useKeyboardShortcuts", () => {
  beforeEach(() => {
    window.localStorage.clear();
    lock.locked = false;
    mockSignedIn([makeProject("proj_1", "Checkout")]);
  });

  afterEach(() => {
    document.querySelectorAll("[data-test-node]").forEach((node) => {
      node.remove();
    });
  });

  it("opens the keyboard help on ?, and ? closes it again", async () => {
    await renderShell();

    await userEvent.keyboard("?");
    const help = await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
    expect(help).toHaveTextContent("Open the command palette");
    expect(help).toHaveTextContent("Next or previous trace in the peek");

    await userEvent.keyboard("?");
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Keyboard shortcuts" })).not.toBeInTheDocument();
    });
  });

  it("ignores ? while typing in an input", async () => {
    await renderShell();
    const input = document.createElement("input");
    input.dataset.testNode = "";
    document.body.append(input);
    input.focus();

    await userEvent.keyboard("?");

    expect(screen.queryByRole("dialog", { name: "Keyboard shortcuts" })).not.toBeInTheDocument();
  });

  it("ignores ? while navigation is locked", async () => {
    await renderShell();
    lock.locked = true;

    await userEvent.keyboard("?");

    expect(screen.queryByRole("dialog", { name: "Keyboard shortcuts" })).not.toBeInTheDocument();
  });

  it("ignores ? while a confirm dialog is open", async () => {
    await renderShell();
    const confirm = document.createElement("div");
    confirm.setAttribute("role", "alertdialog");
    confirm.dataset.testNode = "";
    document.body.append(confirm);

    await userEvent.keyboard("?");

    expect(screen.queryByRole("dialog", { name: "Keyboard shortcuts" })).not.toBeInTheDocument();
  });

  it("ignores ? while the command palette is open", async () => {
    await renderShell();
    await userEvent.keyboard("{Control>}k{/Control}");
    await screen.findByRole("dialog", { name: "Command palette" });

    // Sent to the body, not the palette's input, so only the open-dialog guard can stop it.
    fireEvent.keyDown(document.body, { key: "?" });

    expect(screen.queryByRole("dialog", { name: "Keyboard shortcuts" })).not.toBeInTheDocument();
  });

  it.each([
    ["Control", "{Control>}k{/Control}"],
    ["Meta", "{Meta>}k{/Meta}"],
  ])("closes the help when %s+K opens the palette", async (_modifier, keys) => {
    await renderShell();
    await userEvent.keyboard("?");
    await screen.findByRole("dialog", { name: "Keyboard shortcuts" });

    await userEvent.keyboard(keys);

    await screen.findByRole("dialog", { name: "Command palette" });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Keyboard shortcuts" })).not.toBeInTheDocument();
    });
  });

  it("doesn't jump pages behind the open help", async () => {
    const router = await renderShell();
    await userEvent.keyboard("?");
    await screen.findByRole("dialog", { name: "Keyboard shortcuts" });

    await userEvent.keyboard("gt");

    expect(router.state.location.pathname).toBe("/acme/checkout/settings/project");
  });

  it("still jumps with G then C to Spend and G then , to Settings", async () => {
    const router = await renderShell();

    await userEvent.keyboard("gc");
    await screen.findByText("Spend page");
    expect(router.state.location.pathname).toBe("/acme/checkout/attribution");

    await userEvent.keyboard("g,");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/acme/checkout/settings/project");
    });
  });
});
