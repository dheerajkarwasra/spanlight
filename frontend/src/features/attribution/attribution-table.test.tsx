import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { visibleColumns } from "@/components/data-table";
import { ApiError } from "@/lib/api";
import { mockSignedIn, renderApp } from "@/test/render-app";

import {
  bucket,
  listReads,
  mockAttribution,
  PAGE,
  project,
  response,
  row,
  windowNoteLive,
} from "./attribution-test-data";
import { spendColumns } from "./spend-columns";

const ROLLUP = `${PAGE}?group_by=metadata&key=customer_id&range=30d&env=production`;

async function table() {
  return within(await screen.findByRole("table", { name: "Cost by customer_id" }));
}

function searchOf(link: HTMLElement) {
  return new URL(link.getAttribute("href") ?? "", "http://localhost").searchParams;
}

describe("AttributionTable", () => {
  beforeEach(() => {
    mockSignedIn([project(["customer_id"])]);
  });
  afterEach(() => {
    window.localStorage.removeItem("spanlight.columns.spend");
  });

  it("shows unknown cost, share and retries as a dash with the reason, never 0", async () => {
    // Retries and Retry cost are hidden by default; this viewer turned them on.
    window.localStorage.setItem("spanlight.columns.spend", "retries:1,retry_cost:1");
    mockAttribution(() =>
      Promise.resolve(
        response({
          items: [
            row("internal-evals", {
              cost_usd: null,
              share_of_cost: null,
              unpriced_calls: 64,
              retries: null,
              retry_cost_usd: null,
            }),
          ],
        }),
      ),
    );
    renderApp(ROLLUP);

    const view = await table();
    expect(view.getAllByLabelText("Unknown: No priced calls in this group")).toHaveLength(2);
    expect(
      view.getAllByLabelText(
        "Unknown: Retries are not computed for traces stored before retry tracking was added",
      ),
    ).toHaveLength(2);
    expect(view.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("links a value to the traces with that key and value over the same window and environment", async () => {
    mockAttribution(() => Promise.resolve(response()));
    renderApp(ROLLUP);

    const link = (await table()).getByRole("link", { name: "acme" });
    const search = searchOf(link);
    expect(new URL(link.getAttribute("href") ?? "", "http://localhost").pathname).toBe(
      "/acme/support/traces",
    );
    expect(search.get("metadata_key")).toBe("customer_id");
    expect(search.get("metadata_value")).toBe("acme");
    expect(search.get("from")).toBe("2026-09-16T00:00:00Z");
    expect(search.get("to")).toBe("2026-10-16T00:00:00Z");
    expect(search.get("env")).toBe("production");
  });

  it("links a trace name with the exact-match name filter", async () => {
    mockAttribution(() =>
      Promise.resolve(response({ group_by: "trace_name", key: null, items: [row("summarize")] })),
    );
    renderApp(`${PAGE}?group_by=trace_name&range=30d`);

    const view = within(await screen.findByRole("table", { name: "Cost by trace name" }));
    const search = searchOf(view.getByRole("link", { name: "summarize" }));
    expect(search.get("name")).toBe("summarize");
    expect(search.get("metadata_key")).toBeNull();
  });

  it("renders a redacted value as the redacted badge and drills down on the stored value", async () => {
    mockAttribution(() => Promise.resolve(response({ items: [row("[REDACTED]")] })));
    renderApp(ROLLUP);

    const link = (await table()).getByRole("link", { name: "redacted" });
    expect(within(link).getByText("redacted")).toHaveAttribute("title", "[REDACTED]");
    expect(searchOf(link).get("metadata_value")).toBe("[REDACTED]");
  });

  it("links the none row with the key only", async () => {
    mockAttribution(() => Promise.resolve(response({ none: bucket() })));
    renderApp(ROLLUP);

    const search = searchOf((await table()).getByRole("link", { name: "No customer_id" }));
    expect(search.get("metadata_key")).toBe("customer_id");
    expect(search.has("metadata_value")).toBe(false);
  });

  it("says the rollup source beside the window and names the last day, not the exclusive end", async () => {
    mockAttribution(() => Promise.resolve(response()));
    renderApp(ROLLUP);

    expect(await screen.findByRole("button", { name: /^Updated / })).toBeInTheDocument();
    expect(await screen.findByText("Sep 16 – Oct 15 · 30 days")).toBeInTheDocument();
  });

  it("keeps a real zero retry count as 0, not a dash", async () => {
    mockAttribution(() =>
      Promise.resolve(response({ items: [row("acme", { retries: 0, retry_cost_usd: "0" })] })),
    );
    renderApp(ROLLUP);

    const cells = (await table()).getAllByRole("row")[1];
    expect(cells).toBeDefined();
    expect(within(cells as HTMLElement).queryByLabelText(/^Unknown: Retries/)).toBeNull();
    expect(within(cells as HTMLElement).getAllByText("0").length).toBeGreaterThan(0);
  });

  it("links with the requested window when the widened rollup window passes 90 days", async () => {
    mockAttribution(() =>
      Promise.resolve(
        response({ window: { from: "2026-07-17T00:00:00Z", to: "2026-10-17T00:00:00Z" } }),
      ),
    );
    renderApp(ROLLUP);

    const search = searchOf((await table()).getByRole("link", { name: "acme" }));
    expect(search.get("from")).not.toBe("2026-07-17T00:00:00Z");
    const span = Date.parse(search.get("to") ?? "") - Date.parse(search.get("from") ?? "");
    expect(span).toBe(30 * 24 * 3_600_000);
  });

  it("makes each phone tile one link to its traces", async () => {
    mockAttribution(() =>
      Promise.resolve(response({ other: bucket({ folded_values: 5, cost_usd: "2.10000000" }) })),
    );
    renderApp(ROLLUP);

    const tiles = within(await screen.findByRole("list", { name: "Cost by customer_id" }));
    const tile = tiles.getByRole("link", { name: /^acme/ });
    expect(searchOf(tile).get("metadata_value")).toBe("acme");
    expect(within(tile).getByText("$0.8000")).toBeInTheDocument();
    expect(within(tile).queryByRole("button")).toBeNull();
    const outside = within(screen.getByRole("region", { name: "Outside the list" }));
    expect(outside.getByText("Up to 5 more values, daily cap of 1,000").closest("a")).toBeNull();
  });

  it("says Live for a window of 24 hours or less", async () => {
    mockAttribution(() => Promise.resolve(response({ source: "raw", updated_within_minutes: 0 })));
    renderApp(`${PAGE}?group_by=metadata&key=plan&range=24h`);

    await waitFor(() => {
      expect(windowNoteLive()).toHaveLength(1);
    });
  });

  it("names the key in the empty state and shows how to set it", async () => {
    mockAttribution(() => Promise.resolve(response({ items: [], none: bucket() })));
    renderApp(ROLLUP);

    expect(
      await screen.findByRole("heading", { name: "No traces carried customer_id in this window" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Python SDK: set metadata")).toHaveTextContent(
      'spanlight.update_trace(metadata={"customer_id": ',
    );
    expect(screen.getByText("spanlight.metadata.customer_id")).toBeInTheDocument();
  });

  it("shows the failure with a retry", async () => {
    // A 4xx is not retried by the app's query client (`shouldRetry`), so the error shows at once;
    // a 5xx or network error would first wait out two backoff retries (about 3 s).
    const read = mockAttribution(() =>
      Promise.reject(new ApiError(422, { code: "VALIDATION_ERROR", detail: "Bad window." })),
    );
    renderApp(ROLLUP);

    expect(await screen.findByText("Couldn't load attribution")).toBeInTheDocument();
    expect(listReads(read)).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => {
      expect(listReads(read)).toHaveLength(2);
    });
  });

  it("hides LLM calls and Errors by default when the table box is narrower than every track", () => {
    const columns = spendColumns({
      drill: {
        groupBy: "metadata",
        key: "customer_id",
        window: { from: "", to: "" },
        environment: undefined,
      },
      onFocus: () => undefined,
    });
    const ids = (width: number) => visibleColumns(columns, {}, width).map((col) => col.id);
    // The threshold is every default track's minimum width plus the 40 px Table settings track.
    const fullWidth = columns
      .filter((col) => !col.defaultHidden)
      .reduce((sum, col) => sum + Number(/\d+/.exec(col.width)?.[0]), 40);
    expect(columns.find((col) => col.id === "calls")?.minContainer).toBe(fullWidth);
    // The table box measures 819 px at a 1024 px window and 891 px at 1280 px.
    expect(ids(819)).toEqual(["group", "traces", "unpriced", "cost", "share"]);
    expect(ids(891)).toEqual(["group", "traces", "calls", "errors", "unpriced", "cost", "share"]);
    // A viewer's choice in Table settings still wins.
    expect(visibleColumns(columns, { calls: true }, 819).map((col) => col.id)).toContain("calls");
    expect(columns.filter((col) => col.hideable).map((col) => col.id)).toEqual(
      expect.arrayContaining(["calls", "errors"]),
    );
  });
});
