import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError, authApi, orgsApi } from "@/lib/api";
import { makeProject, ME, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const ACCOUNT = `/${ORG_ID}/proj_1/settings/account`;

function openAccount() {
  mockSignedIn([makeProject("proj_1", "Checkout")]);
  vi.spyOn(orgsApi, "members").mockResolvedValue([]);
  renderApp(ACCOUNT);
}

describe("Settings › Account › Email", () => {
  it("turns the weekly digest off on toggle and keeps it off", async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, "updateMe").mockResolvedValue(ME.user);
    openAccount();

    const digest = await screen.findByRole("switch", { name: "Weekly digest" });
    expect(digest).toBeChecked();
    expect(digest).toHaveAccessibleDescription(
      "A Monday summary of each project's week. Applies to every organization you belong to.",
    );

    await user.click(digest);

    expect(authApi.updateMe).toHaveBeenCalledWith({ weekly_digest: false });
    await waitFor(() => {
      expect(digest).not.toBeChecked();
    });
    expect(digest).toBeEnabled();
  });

  it("flips back and shows the error when saving fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, "updateMe").mockRejectedValue(
      new ApiError(500, { detail: "Something went wrong on our side." }),
    );
    openAccount();

    const digest = await screen.findByRole("switch", { name: "Weekly digest" });
    await user.click(digest);

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on our side.");
    expect(digest).toBeChecked();
  });
});
