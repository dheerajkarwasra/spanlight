import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { ApiError, orgsApi } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

it("shows a wrong Pause password on its field and keeps streaming on", async () => {
  const user = userEvent.setup();
  mockSignedIn([makeProject("proj_1", "Checkout")]);
  vi.spyOn(orgsApi, "members").mockResolvedValue([]);
  vi.spyOn(orgsApi, "audit").mockResolvedValue({ items: [], next_cursor: null });
  vi.spyOn(orgsApi, "auditIntegrity").mockResolvedValue({
    ok: true,
    checked: 0,
    first_seq: null,
    head_seq: null,
    head_hash: null,
    first_broken_seq: null,
  });
  vi.spyOn(orgsApi, "auditStream").mockResolvedValue({
    url: "https://logs.example.com/audit",
    enabled: true,
    delivered_seq: 0,
    head_seq: 0,
    last_delivered_at: null,
    last_error: null,
    last_error_at: null,
    consecutive_failures: 0,
    created_at: "2026-11-08T00:00:00Z",
    updated_at: "2026-11-08T00:00:00Z",
  });
  const save = vi
    .spyOn(orgsApi, "saveAuditStream")
    .mockRejectedValue(
      new ApiError(422, { code: "INVALID_PASSWORD", detail: "That password is not correct." }),
    );
  renderApp("/acme/checkout/settings/audit");
  const toggle = await screen.findByRole("switch", { name: "Stream audit events" });
  await user.click(toggle);
  expect(toggle).toBeChecked();
  expect(save).not.toHaveBeenCalled();
  const dialog = await screen.findByRole("dialog", { name: "Pause streaming?" });
  const password = within(dialog).getByLabelText("Password");
  await user.type(password, "wrong password");
  await user.click(within(dialog).getByRole("button", { name: "Pause" }));

  await waitFor(() => {
    expect(password).toBeInvalid();
    expect(password).toHaveAccessibleDescription("That password is not correct.");
    expect(password).toHaveFocus();
  });
  expect(save).toHaveBeenCalledExactlyOnceWith(ORG_ID, {
    url: "https://logs.example.com/audit",
    enabled: false,
    password: "wrong password",
  });
  expect(toggle).toBeChecked();
  expect(dialog).toBeVisible();
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(toggle).toBeChecked();
  await waitFor(() => {
    expect(toggle).toHaveFocus();
  });
});

it("moves focus to the page once Remove has taken the stream's own controls away", async () => {
  const user = userEvent.setup();
  mockSignedIn([makeProject("proj_1", "Checkout")]);
  vi.spyOn(orgsApi, "members").mockResolvedValue([]);
  vi.spyOn(orgsApi, "audit").mockResolvedValue({ items: [], next_cursor: null });
  vi.spyOn(orgsApi, "auditIntegrity").mockResolvedValue({
    ok: true,
    checked: 0,
    first_seq: null,
    head_seq: null,
    head_hash: null,
    first_broken_seq: null,
  });
  const stream = vi.spyOn(orgsApi, "auditStream").mockResolvedValue({
    url: "https://logs.example.com/audit",
    enabled: true,
    delivered_seq: 0,
    head_seq: 0,
    last_delivered_at: null,
    last_error: null,
    last_error_at: null,
    consecutive_failures: 0,
    created_at: "2026-11-08T00:00:00Z",
    updated_at: "2026-11-08T00:00:00Z",
  });
  vi.spyOn(orgsApi, "deleteAuditStream").mockImplementation(() => {
    stream.mockResolvedValue(null);
    return Promise.resolve();
  });
  renderApp("/acme/checkout/settings/audit");
  await user.click(await screen.findByRole("button", { name: "Remove" }));
  const dialog = await screen.findByRole("dialog");
  await user.type(within(dialog).getByLabelText("Password"), "right password");
  await user.click(within(dialog).getByRole("button", { name: "Remove" }));

  await waitFor(() => {
    expect(document.getElementById("main")).toHaveFocus();
  });
}, 15_000);
