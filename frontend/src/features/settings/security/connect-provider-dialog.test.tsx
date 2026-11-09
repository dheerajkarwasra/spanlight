import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { ApiError, authApi, securityApi } from "@/lib/api";
import { mockSignedIn, renderApp } from "@/test/render-app";

it("shows a wrong password on the field without navigating", async () => {
  const user = userEvent.setup();
  mockSignedIn([]);
  vi.spyOn(securityApi, "oauthProviders").mockResolvedValue([{ provider: "github" }]);
  vi.spyOn(securityApi, "oauthIdentities").mockResolvedValue([]);
  vi.spyOn(securityApi, "totpStatus").mockResolvedValue({
    enabled: false,
    enabled_at: null,
    recovery_codes_remaining: 0,
  });
  vi.spyOn(authApi, "sessions").mockResolvedValue([]);
  const link = vi
    .spyOn(securityApi, "linkOAuth")
    .mockRejectedValue(
      new ApiError(422, { code: "INVALID_PASSWORD", detail: "That password is not correct." }),
    );
  renderApp("/account/security");
  await user.click(await screen.findByRole("button", { name: "Connect GitHub" }));
  const dialog = await screen.findByRole("dialog", { name: "Connect GitHub" });
  const password = within(dialog).getByLabelText("Password");
  const location = window.location.href;
  await user.type(password, "wrong password");
  await user.click(within(dialog).getByRole("button", { name: "Continue to GitHub" }));

  await waitFor(() => {
    expect(password).toBeInvalid();
    expect(password).toHaveAccessibleDescription("That password is not correct.");
    expect(password).toHaveFocus();
  });
  expect(link).toHaveBeenCalledExactlyOnceWith("github", {
    password: "wrong password",
    next: "/account/security",
  });
  expect(window.location.href).toBe(location);
  expect(dialog).toBeVisible();
});

it("lets the person leave the dialog after coming back from the provider", async () => {
  const user = userEvent.setup();
  mockSignedIn([]);
  vi.spyOn(securityApi, "oauthProviders").mockResolvedValue([{ provider: "github" }]);
  vi.spyOn(securityApi, "oauthIdentities").mockResolvedValue([]);
  vi.spyOn(securityApi, "totpStatus").mockResolvedValue({
    enabled: false,
    enabled_at: null,
    recovery_codes_remaining: 0,
  });
  vi.spyOn(authApi, "sessions").mockResolvedValue([]);
  vi.spyOn(securityApi, "linkOAuth").mockResolvedValue({ url: "#at-the-provider" });
  renderApp("/account/security");
  await user.click(await screen.findByRole("button", { name: "Connect GitHub" }));
  const dialog = await screen.findByRole("dialog", { name: "Connect GitHub" });
  await user.type(within(dialog).getByLabelText("Password"), "right password");
  await user.click(within(dialog).getByRole("button", { name: "Continue to GitHub" }));
  await waitFor(() => {
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  // The Back button restores this page from the browser's cache, state and all.
  window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: true }));

  await waitFor(() => {
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeEnabled();
  });
});
