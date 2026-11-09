import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { ApiError, orgsApi, type Member } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

it("keeps the current role when a wrong password is shown on the field", async () => {
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
    read_keys: 0,
    created_at: "2026-11-08T00:00:00Z",
  };
  vi.spyOn(orgsApi, "members").mockResolvedValue([member]);
  vi.spyOn(orgsApi, "invites").mockResolvedValue([]);
  const update = vi
    .spyOn(orgsApi, "updateMemberRole")
    .mockRejectedValue(
      new ApiError(422, { code: "INVALID_PASSWORD", detail: "That password is not correct." }),
    );
  renderApp("/acme/checkout/settings/members");
  const select = await screen.findByRole("combobox", { name: "Role for Priya Raman" });
  await user.click(select);
  await user.click(await screen.findByRole("option", { name: "Admin" }));
  const dialog = await screen.findByRole("dialog", { name: "Make Priya Raman an admin?" });
  const password = within(dialog).getByLabelText("Password");
  await user.type(password, "wrong password");
  await user.click(within(dialog).getByRole("button", { name: "Change role" }));

  await waitFor(() => {
    expect(password).toBeInvalid();
    expect(password).toHaveAccessibleDescription("That password is not correct.");
    expect(password).toHaveFocus();
  });
  expect(update).toHaveBeenCalledExactlyOnceWith(ORG_ID, "u2", {
    role: "admin",
    password: "wrong password",
  });
  expect(select).toHaveTextContent("Member");
  expect(dialog).toBeVisible();
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(select).toHaveTextContent("Member");
  await waitFor(() => {
    expect(select).toHaveFocus();
  });
});
