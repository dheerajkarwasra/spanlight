import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { ApiError, tokensApi } from "@/lib/api";
import { makeProject, mockSignedIn, renderApp } from "@/test/render-app";

it("shows a wrong-password error on the password field without creating a token", async () => {
  const user = userEvent.setup();
  mockSignedIn([makeProject("proj_1", "Checkout")]);
  vi.spyOn(tokensApi, "list").mockResolvedValue([]);
  const create = vi.spyOn(tokensApi, "create").mockRejectedValue(
    new ApiError(422, {
      code: "INVALID_PASSWORD",
      detail: "That password is not correct.",
    }),
  );
  renderApp("/acme/checkout/settings/tokens");

  await screen.findByText("No tokens yet");
  await user.click(await screen.findByRole("button", { name: "Create token" }));
  const dialog = await screen.findByRole("dialog", { name: "Create a token" });
  await user.type(within(dialog).getByRole("textbox", { name: "Name" }), "ci-release");
  const password = within(dialog).getByLabelText("Password");
  await user.type(password, "wrong password");
  await user.click(within(dialog).getByRole("radio", { name: "Never" }));
  await user.click(within(dialog).getByRole("button", { name: "Create token" }));

  await waitFor(() => {
    expect(password).toBeInvalid();
    expect(password).toHaveAccessibleDescription("That password is not correct.");
    expect(password).toHaveFocus();
  });
  expect(create).toHaveBeenCalledExactlyOnceWith({
    name: "ci-release",
    scope: "read",
    expires_at: null,
    password: "wrong password",
  });
  expect(screen.getByRole("dialog", { name: "Create a token" })).toBeVisible();
  expect(screen.getByText("No tokens yet")).toBeInTheDocument();
  expect(tokensApi.list).toHaveBeenCalledTimes(1);
});

it("keeps the first token's secret on screen when the list stops being empty", async () => {
  const user = userEvent.setup();
  mockSignedIn([makeProject("proj_1", "Checkout")]);
  const token = {
    id: "tok_1",
    name: "ci-release",
    prefix: "spl_pat_abc",
    scope: "read" as const,
    created_at: "2026-11-08T00:00:00Z",
    expires_at: null,
    last_used_at: null,
  };
  const list = vi.spyOn(tokensApi, "list").mockResolvedValue([]);
  vi.spyOn(tokensApi, "create").mockImplementation(() => {
    list.mockResolvedValue([token]);
    return Promise.resolve({ ...token, token: "spl_pat_abc_the_whole_secret" });
  });
  renderApp("/acme/checkout/settings/tokens");

  await screen.findByText("No tokens yet");
  await user.click(await screen.findByRole("button", { name: "Create token" }));
  const dialog = await screen.findByRole("dialog", { name: "Create a token" });
  await user.type(within(dialog).getByRole("textbox", { name: "Name" }), "ci-release");
  await user.type(within(dialog).getByLabelText("Password"), "right password");
  await user.click(within(dialog).getByRole("button", { name: "Create token" }));

  expect(await screen.findByText("spl_pat_abc_the_whole_secret")).toBeVisible();
  // The refresh lands while the dialog is open and removes the empty state behind it.
  await waitFor(() => {
    expect(screen.queryByText("No tokens yet")).not.toBeInTheDocument();
  });
  expect(screen.getByText("spl_pat_abc_the_whole_secret")).toBeVisible();

  // The button that opened the dialog is gone with the empty state.
  await user.click(screen.getByRole("button", { name: "Done" }));
  await waitFor(() => {
    expect(document.getElementById("main")).toHaveFocus();
  });
});

it("returns focus to the button that opened the dialog", async () => {
  const user = userEvent.setup();
  mockSignedIn([makeProject("proj_1", "Checkout")]);
  vi.spyOn(tokensApi, "list").mockResolvedValue([]);
  renderApp("/acme/checkout/settings/tokens");

  await screen.findByText("No tokens yet");
  const opener = await screen.findByRole("button", { name: "Create token" });
  await user.click(opener);
  const dialog = await screen.findByRole("dialog", { name: "Create a token" });
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

  await waitFor(() => {
    expect(opener).toHaveFocus();
  });
});
