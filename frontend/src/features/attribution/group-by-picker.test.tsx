import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { authApi } from "@/lib/api";
import { ME, mockSignedIn, renderApp } from "@/test/render-app";

import { mockAttribution, PAGE, project, response } from "./attribution-test-data";
import { isFetchDisabled } from "./fetch-rule";

describe("isFetchDisabled", () => {
  it("holds the fetch for a key that is not promoted over a window longer than 24 hours", () => {
    expect(isFetchDisabled("metadata", "region", ["customer_id"], 720)).toBe(true);
  });

  it("fetches a promoted key over any window, and any key over 24 hours or less", () => {
    expect(isFetchDisabled("metadata", "customer_id", ["customer_id"], 720)).toBe(false);
    expect(isFetchDisabled("metadata", "region", [], 24)).toBe(false);
  });

  it("holds the fetch for metadata without a key, which the API would refuse", () => {
    expect(isFetchDisabled("metadata", undefined, ["customer_id"], 1)).toBe(true);
  });

  it("never holds the fetch for the other kinds", () => {
    expect(isFetchDisabled("trace_name", undefined, [], 720)).toBe(false);
    expect(isFetchDisabled("user", undefined, [], 720)).toBe(false);
  });
});

describe("GroupByPicker on the page", () => {
  beforeEach(() => {
    mockSignedIn([project(["customer_id"])]);
  });

  it("shows the promote note in place of the table and sends no request (admin)", async () => {
    const read = mockAttribution(() => Promise.resolve(response()));
    renderApp(`${PAGE}?group_by=metadata&key=region&range=30d`);

    expect(
      await screen.findByText(/isn’t promoted, so this window can’t use it/),
    ).toBeInTheDocument();
    const promote = screen.getByRole("link", { name: "Promote" });
    expect(promote.getAttribute("href")).toMatch(/^\/acme\/support\/settings\/project(\?|$)/);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(read).not.toHaveBeenCalled();
  });

  it("asks a member to ask an admin instead of linking to settings", async () => {
    vi.spyOn(authApi, "me").mockResolvedValue({
      ...ME,
      memberships: ME.memberships.map((each) => ({ ...each, role: "member" as const })),
    });
    const read = mockAttribution(() => Promise.resolve(response()));
    renderApp(`${PAGE}?group_by=metadata&key=region&range=30d`);

    expect(await screen.findByText(/Ask an admin to promote/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Promote" })).not.toBeInTheDocument();
    expect(read).not.toHaveBeenCalled();
  });

  it("drops the previous window's page when the fetch is held", async () => {
    const read = mockAttribution(() =>
      Promise.resolve(response({ key: "region", source: "raw", updated_within_minutes: 0 })),
    );
    const router = renderApp(`${PAGE}?group_by=metadata&key=region&range=24h`);
    await waitFor(() => {
      expect(read).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByText("Live")).toBeInTheDocument();

    await router.navigate({
      to: ".",
      search: (previous: Record<string, unknown>) => ({ ...previous, range: "30d" }),
    } as never);

    expect(
      await screen.findByText(/isn’t promoted, so this window can’t use it/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Live")).not.toBeInTheDocument();
    expect(screen.getByText("Updated every 15 min")).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(1);
  });

  it("asks for a key and sends no request when metadata has none", async () => {
    const read = mockAttribution(() => Promise.resolve(response()));
    renderApp(`${PAGE}?group_by=metadata`);

    expect(await screen.findByRole("heading", { name: "Pick a metadata key" })).toBeInTheDocument();
    expect(read).not.toHaveBeenCalled();
  });

  it("lists promoted keys first, marked promoted, then the keys seen in 7 days", async () => {
    mockAttribution(() => Promise.resolve(response()));
    renderApp(`${PAGE}?group_by=metadata&key=customer_id&range=30d`);

    const trigger = await screen.findByRole("combobox", { name: "Metadata key" });
    expect(trigger).toHaveTextContent("customer_id");
    expect(await within(trigger).findByText("promoted")).toBeInTheDocument();
  });
});
