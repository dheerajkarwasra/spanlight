import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  authApi,
  priceOverridesApi,
  pricesApi,
  type Price,
  type PriceOverride,
} from "@/lib/api";
import { makeProject, ME, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

// West of UTC before any module loads: the app's date formatters are built at import and keep
// the zone they were built in, so a stub inside a test would come too late.
vi.hoisted(() => {
  vi.stubEnv("TZ", "America/Los_Angeles");
});
afterAll(() => {
  vi.unstubAllEnvs();
});

vi.mock("@/features/attribution/cost-strip", () => ({ CostStrip: () => null }));

const PATH = `/${ORG_ID}/proj_1/pricing`;

const PRICE: Price = {
  provider: "anthropic",
  model_pattern: "claude-sonnet-4-5",
  input_per_mtok: "3.000000",
  output_per_mtok: "15.000000",
  cached_input_per_mtok: "0.300000",
  cache_write_5m_per_mtok: "3.750000",
  cache_write_1h_per_mtok: null,
  min_prompt_tokens: 0,
  effective_from: "2026-09-29T00:00:00Z",
  version: "v14",
};

const OVERRIDE: PriceOverride = {
  id: "po_1",
  provider: "openai",
  model_pattern: "gpt-4.1-batch",
  input_per_mtok: "1.000000",
  output_per_mtok: "4.000000",
  cached_input_per_mtok: null,
  cache_write_5m_per_mtok: null,
  cache_write_1h_per_mtok: null,
  min_prompt_tokens: 0,
  effective_from: "2026-09-18T00:00:00Z",
  created_by: null,
  created_at: "2026-09-18T00:00:00Z",
};

function asRole(role: "viewer" | "owner") {
  const membership = ME.memberships[0];
  if (membership === undefined) {
    throw new Error("ME has no membership");
  }
  vi.spyOn(authApi, "me").mockResolvedValue({ ...ME, memberships: [{ ...membership, role }] });
}

describe("PricingPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(pricesApi, "list").mockResolvedValue([
      { ...PRICE, effective_from: "2025-01-01T00:00:00Z", version: "v3", input_per_mtok: "9" },
      PRICE,
    ]);
    vi.spyOn(pricesApi, "unpricedModels").mockResolvedValue([
      {
        provider: "openai",
        model: "ft:custom-v2",
        llm_calls: 4,
        input_tokens: 9100,
        output_tokens: 1000,
      },
    ]);
    vi.spyOn(priceOverridesApi, "list").mockResolvedValue([OVERRIDE]);
  });

  it("lists the window's unpriced models for the page's environment, the overrides and the newest book prices", async () => {
    renderApp(`${PATH}?env=prod`);

    const unpriced = await screen.findByRole("region", { name: "Unpriced models" });
    expect((await within(unpriced).findAllByText("ft:custom-v2")).length).toBeGreaterThan(0);
    const [projectId, window, environment] =
      vi.mocked(pricesApi.unpricedModels).mock.calls[0] ?? [];
    expect([projectId, Object.keys(window ?? {}), environment]).toEqual([
      "proj_1",
      ["from", "to"],
      "prod",
    ]);
    const overrides = screen.getByRole("region", { name: "Organization overrides" });
    expect((await within(overrides).findAllByText("gpt-4.1-batch")).length).toBeGreaterThan(0);

    const book = await screen.findByRole("table", { name: "Price book" });
    // The sticky Model pattern comes first, so no scrolling column sits on its left.
    expect(
      within(book)
        .getAllByRole("columnheader")
        .slice(0, 2)
        .map((header) => header.textContent),
    ).toEqual(["Model pattern", "Provider"]);
    // Short cache-write headers, so neither truncates in its 110 px column.
    expect(within(book).getByRole("columnheader", { name: "Write 5 m" })).toBeInTheDocument();
    expect(within(book).getByRole("columnheader", { name: "Write 1 h" })).toBeInTheDocument();
    expect(within(book).getAllByText("claude-sonnet-4-5")).toHaveLength(1);
    expect(within(book).getByText("$3.00")).toBeInTheDocument();
    expect(within(book).getByText("v14")).toBeInTheDocument();
  });

  it("opens the dialog prefilled from the URL once and clears the params on close", async () => {
    const router = renderApp(`${PATH}?provider=openai&model=ft:custom-v2&range=7d`);
    const user = userEvent.setup();

    const dialog = await screen.findByRole("dialog", { name: "Add price override" });
    expect(within(dialog).getByLabelText("Provider")).toHaveValue("openai");
    expect(within(dialog).getByLabelText("Model pattern")).toHaveValue("ft:custom-v2");

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(router.state.location.search).toEqual({ range: "7d" });
  });

  it("prefills the dialog from an unpriced model's Add price", async () => {
    renderApp(PATH);
    const user = userEvent.setup();

    const unpriced = await screen.findByRole("region", { name: "Unpriced models" });
    const [addPrice] = await within(unpriced).findAllByRole("button", {
      name: "Add price for ft:custom-v2",
    });
    await user.click(addPrice as HTMLElement);

    const dialog = await screen.findByRole("dialog", { name: "Add price override" });
    expect(within(dialog).getByLabelText("Model pattern")).toHaveValue("ft:custom-v2");
  });

  it("puts an existing override's 409 on the pattern field", async () => {
    vi.spyOn(priceOverridesApi, "create").mockRejectedValue(
      new ApiError(409, { code: "PRICE_OVERRIDE_EXISTS" }),
    );
    renderApp(`${PATH}?provider=openai&model=gpt-4.1-batch`);
    const user = userEvent.setup();

    const dialog = await screen.findByRole("dialog", { name: "Add price override" });
    await user.type(within(dialog).getByLabelText("Input per 1M"), "1");
    await user.type(within(dialog).getByLabelText("Output per 1M"), "4");
    await user.click(within(dialog).getByRole("button", { name: "Add override" }));

    expect(
      await within(dialog).findByText("An override for this provider and pattern already exists."),
    ).toBeInTheDocument();
    expect(priceOverridesApi.create).toHaveBeenCalledWith(
      ORG_ID,
      expect.objectContaining({ provider: "openai", input_per_mtok: "1", output_per_mtok: "4" }),
    );
  });

  it("dates an override by its UTC start day, also west of UTC", async () => {
    renderApp(PATH);

    const overrides = await screen.findByRole("region", { name: "Organization overrides" });
    expect(await within(overrides).findByText(/^From Sep 18\b/)).toBeInTheDocument();
  });

  it("warns that a pattern with capitals is saved lower-case and won't match those calls", async () => {
    renderApp(`${PATH}?provider=together&model=Meta-Llama-3`);
    const user = userEvent.setup();

    const dialog = await screen.findByRole("dialog", { name: "Add price override" });
    const pattern = within(dialog).getByLabelText("Model pattern");
    expect(pattern).toHaveAccessibleDescription(
      "Saved as meta-llama-3. Model names match exactly, case included, so calls reporting Meta-Llama-3 won't use this price.",
    );
    await user.clear(pattern);
    await user.type(pattern, "meta-llama-3");
    expect(pattern).toHaveAccessibleDescription("Exact model or with a snapshot suffix.");
  });

  it("deletes an override after confirming that stored costs stay", async () => {
    const remove = vi.spyOn(priceOverridesApi, "delete").mockResolvedValue(undefined);
    renderApp(PATH);
    const user = userEvent.setup();

    const overrides = await screen.findByRole("region", { name: "Organization overrides" });
    await user.click(
      await within(overrides).findByRole("button", { name: "Delete override for gpt-4.1-batch" }),
    );
    const confirm = await screen.findByRole("alertdialog");
    expect(confirm).toHaveTextContent(
      "Stored costs are not recomputed; new spans use the book price.",
    );
    await user.click(within(confirm).getByRole("button", { name: "Delete override" }));
    await waitFor(() => {
      expect(remove).toHaveBeenCalledWith(ORG_ID, "po_1");
    });
  });

  it("shows a viewer the prices without Add or Delete, and explains why in the dialog", async () => {
    asRole("viewer");
    renderApp(`${PATH}?provider=openai&model=ft:custom-v2`);

    const dialog = await screen.findByRole("dialog", { name: "Add price override" });
    expect(dialog).toHaveTextContent("You have view-only access.");
    expect(within(dialog).queryByLabelText("Provider")).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));

    await screen.findByRole("table", { name: "Price book" });
    expect(screen.queryByRole("button", { name: /^Add/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Delete override/ })).not.toBeInTheDocument();
  });
});
