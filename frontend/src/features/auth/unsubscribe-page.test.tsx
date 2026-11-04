import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { authApi } from "@/lib/api";
import { mockSignedOut, renderApp } from "@/test/render-app";

const TOKEN = `${"a1".repeat(16)}.${"B".repeat(43)}`;

async function expectTitle(title: string) {
  await waitFor(() => {
    expect(document.title).toBe(`${title} · Spanlight`);
  });
}

describe("unsubscribe page", () => {
  it("changes nothing on open, then posts the fragment token when the button is pressed", async () => {
    const user = userEvent.setup();
    mockSignedOut();
    vi.spyOn(authApi, "unsubscribe").mockResolvedValue({ status: "accepted" });
    renderApp(`/unsubscribe#token=${TOKEN}`);

    const button = await screen.findByRole("button", {
      name: "Unsubscribe from the weekly digest",
    });
    expect(authApi.unsubscribe).not.toHaveBeenCalled();
    expect(window.location.hash).toBe("");
    await expectTitle("Unsubscribe");

    await user.click(button);

    expect(authApi.unsubscribe).toHaveBeenCalledWith(TOKEN);
    const heading = await screen.findByRole("heading", { name: "You're unsubscribed." });
    expect(heading).toHaveFocus();
    await expectTitle("Unsubscribed");
    // Non-breaking spaces keep the link on one line.
    expect(screen.getByRole("link", { name: /^Settings\s›\sAccount$/ })).toBeInTheDocument();
  });

  it.each(["/unsubscribe", "/unsubscribe#token=not-a-token"])(
    "says %s is not valid and sends nothing",
    async (path) => {
      mockSignedOut();
      vi.spyOn(authApi, "unsubscribe");
      renderApp(path);

      expect(await screen.findByRole("alert")).toHaveTextContent("This link is not valid.");
      await expectTitle("Link not valid");
      expect(screen.getByRole("link", { name: "Open Settings › Account" })).toBeInTheDocument();
      expect(authApi.unsubscribe).not.toHaveBeenCalled();
    },
  );
});
