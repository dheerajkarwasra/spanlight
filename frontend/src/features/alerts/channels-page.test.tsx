import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { alertsApi, type AlertChannelWithSecret } from "@/lib/api";
import { makeProject, mockSignedIn, renderApp } from "@/test/render-app";

it("keeps a first webhook's signing secret on screen when the list stops being empty", async () => {
  const user = userEvent.setup();
  mockSignedIn([makeProject("proj_1", "Checkout")]);
  const channel: AlertChannelWithSecret = {
    id: "ch_1",
    org_id: "org_1",
    kind: "webhook",
    name: "ops",
    config: { url: "https://example.com/hook" },
    has_secret: true,
    verified_at: null,
    created_at: "2026-11-08T00:00:00Z",
    updated_at: "2026-11-08T00:00:00Z",
    secret: "whsec_the_whole_secret",
  };
  const list = vi.spyOn(alertsApi, "channels").mockResolvedValue([]);
  vi.spyOn(alertsApi, "createChannel").mockImplementation(() => {
    list.mockResolvedValue([channel]);
    return Promise.resolve(channel);
  });
  renderApp("/acme/checkout/settings/channels");

  await screen.findByText("No channels");
  const [, emptyButton] = screen.getAllByRole("button", { name: "Add channel" });
  if (!emptyButton) {
    throw new Error("the empty state has no add button");
  }
  await user.click(emptyButton);
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("radio", { name: "Webhook" }));
  await user.type(within(dialog).getByLabelText("Name"), "ops");
  await user.type(within(dialog).getByLabelText("Endpoint URL"), "https://example.com/hook");
  await user.click(within(dialog).getByRole("button", { name: /add channel|save/i }));

  expect(await screen.findByText("whsec_the_whole_secret")).toBeVisible();
  // The refresh lands while the dialog is open and removes the empty state behind it.
  await waitFor(() => {
    expect(screen.queryByText("No channels")).not.toBeInTheDocument();
  });
  expect(screen.getByText("whsec_the_whole_secret")).toBeVisible();
}, 15_000);
