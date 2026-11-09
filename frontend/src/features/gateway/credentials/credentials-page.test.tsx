import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, authApi, credentialsApi, gatewayApi } from "@/lib/api";
import { makeProject, ME, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PATH = `/${ORG_ID}/proj_1/gateway/credentials`;

describe("GatewayCredentialsPage", () => {
  beforeEach(() => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(credentialsApi, "list").mockResolvedValue([]);
    vi.spyOn(gatewayApi, "routes").mockResolvedValue([]);
  });

  it("says up front that the server key isn't set and disables Add, with no failed POST", async () => {
    vi.spyOn(authApi, "me").mockResolvedValue({
      ...ME,
      capabilities: { ...ME.capabilities, credentials_configured: false },
    });
    const create = vi.spyOn(credentialsApi, "create");
    renderApp(PATH);

    expect(await screen.findByText("Not configured on this server")).toBeInTheDocument();
    // The header and the empty state both offer Add.
    const buttons = screen.getAllByRole("button", { name: "Add credential" });
    for (const add of buttons) {
      expect(add).toHaveAttribute("aria-disabled", "true");
    }
    const [add] = buttons;
    expect(add).toHaveAccessibleDescription(/CREDENTIALS_KEYS/);

    await userEvent.click(add as HTMLElement);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });

  it("offers Add when the server key is set", async () => {
    renderApp(PATH);
    const [add] = await screen.findAllByRole("button", { name: "Add credential" });
    expect(add).not.toHaveAttribute("aria-disabled");
    expect(screen.queryByText("Not configured on this server")).not.toBeInTheDocument();
  });

  it("keeps the typed API key when only the password was wrong, so a retry just works", async () => {
    const user = userEvent.setup();
    const create = vi
      .spyOn(credentialsApi, "create")
      .mockRejectedValueOnce(
        new ApiError(422, { code: "INVALID_PASSWORD", detail: "That password is not correct." }),
      );
    create.mockResolvedValueOnce({
      id: "cred_1",
      name: "custom-host",
      provider: "openai_compatible",
      base_url: "https://llm.example.com/v1",
      created_by: null,
      created_at: "2026-11-08T00:00:00Z",
      last_used_at: null,
      last_checked_at: null,
      last_error: null,
      rotated_at: null,
    } as never);
    renderApp(PATH);
    const [add] = await screen.findAllByRole("button", { name: "Add credential" });
    await user.click(add as HTMLElement);
    const fields = within(await screen.findByRole("dialog", { name: "Add credential" }));
    await user.type(fields.getByLabelText("Name"), "custom-host");
    await user.click(fields.getByRole("radio", { name: "OpenAI-compatible" }));
    await user.type(fields.getByLabelText("Base URL"), "https://llm.example.com/v1");
    await user.type(fields.getByLabelText("API key"), "sk-test");
    const password = fields.getByLabelText("Password");
    await user.type(password, "wrong");
    await user.click(fields.getByRole("button", { name: "Add credential" }));
    await waitFor(() => {
      expect(password).toBeInvalid();
    });

    await user.clear(password);
    await user.type(password, "right");
    await user.click(fields.getByRole("button", { name: "Add credential" }));

    await waitFor(() => {
      expect(create).toHaveBeenCalledTimes(2);
    });
    expect(create).toHaveBeenLastCalledWith(
      ORG_ID,
      expect.objectContaining({ api_key: "sk-test", password: "right" }),
    );
  }, 15_000);

  it("confirms a custom host, keeps wrong-password errors on the field, and omits proof for OpenAI", async () => {
    const user = userEvent.setup();
    const create = vi.spyOn(credentialsApi, "create").mockRejectedValue(
      new ApiError(422, {
        code: "INVALID_PASSWORD",
        detail: "That password is not correct.",
      }),
    );
    renderApp(PATH);
    const [add] = await screen.findAllByRole("button", { name: "Add credential" });
    await user.click(add as HTMLElement);
    const dialog = await screen.findByRole("dialog", { name: "Add credential" });
    const fields = within(dialog);
    expect(fields.queryByLabelText("Password")).not.toBeInTheDocument();
    await user.type(fields.getByLabelText("Name"), "custom-host");
    await user.click(fields.getByRole("radio", { name: "OpenAI-compatible" }));
    await user.type(fields.getByLabelText("Base URL"), "https://llm.example.com/v1");
    await user.type(fields.getByLabelText("API key"), "sk-test");
    const password = fields.getByLabelText("Password");
    await user.type(password, "wrong password");
    await user.click(fields.getByRole("button", { name: "Add credential" }));

    await waitFor(() => {
      expect(password).toBeInvalid();
      expect(password).toHaveAccessibleDescription("That password is not correct.");
      expect(password).toHaveFocus();
    });
    expect(create).toHaveBeenCalledExactlyOnceWith(ORG_ID, {
      name: "custom-host",
      provider: "openai_compatible",
      api_key: "sk-test",
      base_url: "https://llm.example.com/v1",
      password: "wrong password",
    });
    expect(dialog).toBeVisible();
    expect(screen.getByText("No provider credentials")).toBeInTheDocument();
    expect(credentialsApi.list).toHaveBeenCalledTimes(1);

    await user.click(fields.getByRole("radio", { name: "OpenAI" }));
    expect(fields.queryByLabelText("Password")).not.toBeInTheDocument();
    await user.click(fields.getByRole("radio", { name: "OpenAI-compatible" }));
    expect(fields.getByLabelText("Password")).toHaveValue("");
    await user.click(fields.getByRole("radio", { name: "OpenAI" }));
    await user.clear(fields.getByLabelText("API key"));
    await user.type(fields.getByLabelText("API key"), "sk-openai");
    create.mockResolvedValueOnce({
      id: "cred_1",
      name: "custom-host",
      provider: "openai",
      base_url: null,
      created_by: null,
      created_at: "2026-11-08T00:00:00Z",
      last_used_at: null,
      last_checked_at: null,
      last_error: null,
      rotated_at: null,
    });
    await user.click(fields.getByRole("button", { name: "Add credential" }));
    await waitFor(() => {
      expect(create).toHaveBeenLastCalledWith(ORG_ID, {
        name: "custom-host",
        provider: "openai",
        api_key: "sk-openai",
      });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});
