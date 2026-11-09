import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { ApiError, orgsApi, type Member } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

it("sends the proof and keeps a wrong password on the field in the open dialog", async () => {
  const user = userEvent.setup();
  mockSignedIn([makeProject("proj_1", "Checkout")]);
  const member: Member = {
    user: {
      id: "u2",
      name: "Priya Raman",
      email: "priya@example.com",
      created_at: "",
      email_verified: true,
    },
    role: "member",
    read_keys: 1,
    created_at: "2026-11-08T00:00:00Z",
  };
  vi.spyOn(orgsApi, "members").mockResolvedValue([member]);
  vi.spyOn(orgsApi, "invites").mockResolvedValue([]);
  const remove = vi
    .spyOn(orgsApi, "removeMember")
    .mockRejectedValue(
      new ApiError(422, { code: "INVALID_PASSWORD", detail: "That password is not correct." }),
    );
  renderApp("/acme/checkout/settings/members");
  const trigger = await screen.findByRole("button", { name: "Remove Priya Raman" });
  await user.click(trigger);
  const dialog = await screen.findByRole("dialog", { name: "Remove Priya Raman from Acme?" });
  const password = within(dialog).getByLabelText("Password");
  await waitFor(() => expect(password).toHaveFocus());
  await user.type(password, "wrong password");
  await user.click(within(dialog).getByRole("button", { name: "Remove member" }));

  await waitFor(() => {
    expect(password).toBeInvalid();
    expect(password).toHaveAccessibleDescription("That password is not correct.");
    expect(password).toHaveFocus();
  });
  expect(remove).toHaveBeenCalledExactlyOnceWith(ORG_ID, "u2", { password: "wrong password" });
  expect(dialog).toBeVisible();
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(trigger).toHaveFocus());
});
