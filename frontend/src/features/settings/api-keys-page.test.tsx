import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { projectsApi, type CreatedApiKey } from "@/lib/api";
import { makeProject, mockSignedIn, renderApp } from "@/test/render-app";

it("keeps the first key's secret on screen when the list stops being empty", async () => {
  const user = userEvent.setup();
  mockSignedIn([makeProject("proj_1", "Checkout")]);
  const key: CreatedApiKey = {
    id: "key_1",
    name: "production-api",
    prefix: "spl_live_abc",
    scopes: ["ingest:write"],
    created_by: null,
    created_at: "2026-11-08T00:00:00Z",
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
    secret: "spl_live_abc_the_whole_secret",
  };
  const list = vi.spyOn(projectsApi, "keys").mockResolvedValue([]);
  vi.spyOn(projectsApi, "createKey").mockImplementation(() => {
    list.mockResolvedValue([key]);
    return Promise.resolve(key);
  });
  renderApp("/acme/checkout/settings/keys");

  const empty = (await screen.findByText("No API keys yet")).parentElement?.parentElement;
  if (!empty) {
    throw new Error("empty state not found");
  }
  await user.click(within(empty).getByRole("button", { name: "Create key" }));
  const dialog = await screen.findByRole("dialog");
  await user.type(within(dialog).getByLabelText("Name"), "production-api");
  await user.click(within(dialog).getByRole("button", { name: "Create key" }));

  expect(await screen.findByText("spl_live_abc_the_whole_secret")).toBeVisible();
  // The refresh lands while the dialog is open and removes the empty state behind it.
  await waitFor(() => {
    expect(screen.queryByText("No API keys yet")).not.toBeInTheDocument();
  });
  expect(screen.getByText("spl_live_abc_the_whole_secret")).toBeVisible();
});
