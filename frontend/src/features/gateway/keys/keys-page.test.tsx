import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import {
  ApiError,
  gatewayApi,
  type CreatedGatewayKey,
  type GatewayKey,
  type Route,
} from "@/lib/api";
import { makeProject, mockSignedIn, renderApp } from "@/test/render-app";

it("keeps the first key's secret on screen when the list stops being empty", async () => {
  const user = userEvent.setup();
  mockSignedIn([makeProject("proj_1", "Checkout")]);
  const key: CreatedGatewayKey = {
    id: "key_1",
    name: "checkout-prod",
    prefix: "spl_gw_abc",
    environment: "production",
    route_id: null,
    rpm_limit: null,
    tpm_limit: null,
    cache_ttl_seconds: null,
    allowed_models: [],
    default_tags: [],
    fault_profile_id: null,
    creator_is_member: false,
    created_by: null,
    created_at: "2026-11-08T00:00:00Z",
    last_used_at: null,
    revoked_at: null,
    secret: "spl_gw_abc_the_whole_secret",
  };
  const list = vi.spyOn(gatewayApi, "keys").mockResolvedValue([]);
  const route = { id: "route_1", name: "Default", is_default: true } as Route;
  vi.spyOn(gatewayApi, "routes").mockResolvedValue([route]);
  vi.spyOn(gatewayApi, "faultProfiles").mockResolvedValue([]);
  vi.spyOn(gatewayApi, "createKey").mockImplementation(() => {
    list.mockResolvedValue([key]);
    return Promise.resolve(key);
  });
  await renderApp("/acme/checkout/gateway/keys").load();

  await screen.findByText("No gateway keys");
  const [, emptyButton] = screen.getAllByRole("button", { name: "Create key" });
  if (!emptyButton) {
    throw new Error("the empty state has no create button");
  }
  await user.click(emptyButton);
  const dialog = await screen.findByRole("dialog");
  await user.type(within(dialog).getByLabelText("Name"), "checkout-prod");
  await user.type(within(dialog).getByLabelText("Password"), "correct horse battery");
  await user.click(within(dialog).getByRole("button", { name: "Create key" }));

  expect(await screen.findByText("spl_gw_abc_the_whole_secret")).toBeVisible();
  // The refresh lands while the dialog is open and removes the empty state behind it.
  await waitFor(() => {
    expect(screen.queryByText("No gateway keys")).not.toBeInTheDocument();
  });
  expect(screen.getByText("spl_gw_abc_the_whole_secret")).toBeVisible();
}, 15_000);

it("keeps a wrong password on the field and creates no key", async () => {
  const user = userEvent.setup();
  mockSignedIn([makeProject("proj_1", "Checkout")]);
  vi.spyOn(gatewayApi, "keys").mockResolvedValue([]);
  vi.spyOn(gatewayApi, "routes").mockResolvedValue([
    { id: "route_1", name: "Default", is_default: true } as Route,
  ]);
  vi.spyOn(gatewayApi, "faultProfiles").mockResolvedValue([]);
  const create = vi
    .spyOn(gatewayApi, "createKey")
    .mockRejectedValue(
      new ApiError(422, { code: "INVALID_PASSWORD", detail: "That password is not correct." }),
    );
  await renderApp("/acme/checkout/gateway/keys").load();
  await screen.findByText("No gateway keys");
  const [button] = screen.getAllByRole("button", { name: "Create key" });
  if (!button) throw new Error("the page has no create button");
  await user.click(button);
  const dialog = await screen.findByRole("dialog", { name: "Create gateway key" });
  await user.type(within(dialog).getByLabelText("Name"), "checkout-prod");
  const password = within(dialog).getByLabelText("Password");
  await user.type(password, "wrong password");
  await user.click(within(dialog).getByRole("button", { name: "Create key" }));
  await waitFor(() => {
    expect(password).toBeInvalid();
    expect(password).toHaveAccessibleDescription("That password is not correct.");
  });
  expect(create).toHaveBeenCalledOnce();
  expect(create).toHaveBeenCalledWith(
    "proj_1",
    expect.objectContaining({ password: "wrong password" }),
  );
  expect(dialog).toBeVisible();
  expect(screen.getByText("No gateway keys")).toBeVisible();
  expect(screen.queryByRole("dialog", { name: "Key created" })).not.toBeInTheDocument();
});

it("marks a key whose known creator left, and not a key with no creator", async () => {
  mockSignedIn([makeProject("proj_1", "Checkout")]);
  const base = {
    prefix: "spl_gw_abc",
    environment: "production",
    route_id: null,
    rpm_limit: null,
    tpm_limit: null,
    cache_ttl_seconds: null,
    allowed_models: [],
    default_tags: [],
    fault_profile_id: null,
    creator_is_member: false,
    created_at: "2026-11-08T00:00:00Z",
    last_used_at: null,
    revoked_at: null,
  };
  const user = {
    id: "u1",
    email: "gone@example.com",
    name: "Gone",
    created_at: "2026-11-08T00:00:00Z",
  };
  vi.spyOn(gatewayApi, "keys").mockResolvedValue([
    { ...base, id: "k1", name: "left", created_by: user },
    { ...base, id: "k2", name: "demo", created_by: null },
  ] as unknown as GatewayKey[]);
  vi.spyOn(gatewayApi, "routes").mockResolvedValue([]);
  vi.spyOn(gatewayApi, "faultProfiles").mockResolvedValue([]);
  renderApp("/acme/checkout/gateway/keys");

  await screen.findByText("left");
  expect(screen.getAllByText("Creator left")).toHaveLength(1);
});
