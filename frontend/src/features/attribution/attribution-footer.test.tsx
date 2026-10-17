import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { mockSignedIn, renderApp } from "@/test/render-app";

import { bucket, mockAttribution, PAGE, project, response, row } from "./attribution-test-data";

const ROLLUP = `${PAGE}?group_by=metadata&key=customer_id&range=30d`;

async function table() {
  return within(await screen.findByRole("table", { name: "Cost by customer_id" }));
}

describe("AttributionFooter", () => {
  beforeEach(() => {
    mockSignedIn([project(["customer_id"])]);
  });

  it("shows the folded values past the daily cap, without a link, and explains the cap", async () => {
    mockAttribution(() =>
      Promise.resolve(
        response({ none: bucket(), other: bucket({ folded_values: 212, traces: 300 }) }),
      ),
    );
    renderApp(ROLLUP);

    const view = await table();
    const other = view.getByText("Up to 212 more values, daily cap of 1,000");
    expect(other.closest("a")).toBeNull();
    await userEvent.hover(view.getByRole("button", { name: "Why up to 212 values are folded" }));
    expect(
      (await screen.findAllByText(/Each UTC day keeps the 1,000 costliest values per key/))[0],
    ).toBeInTheDocument();
    expect(screen.getByText(/plus “No customer_id” and the folded values\./)).toBeInTheDocument();
  });

  it("never prints 0 for an unknown folded count", async () => {
    mockAttribution(() => Promise.resolve(response({ other: bucket({ folded_values: null }) })));
    renderApp(ROLLUP);

    const view = await table();
    expect(view.getByText("More values, daily cap of 1,000")).toBeInTheDocument();
    expect(view.queryByText(/\b0 more values/)).not.toBeInTheDocument();
  });

  it("leaves the other row out when nothing was folded", async () => {
    mockAttribution(() => Promise.resolve(response({ none: bucket() })));
    renderApp(ROLLUP);

    const view = await table();
    expect(view.queryByText(/more values?, daily cap of 1,000/i)).not.toBeInTheDocument();
    expect(screen.getByText(/plus “No customer_id”\./)).toBeInTheDocument();
  });

  it("shows the exact totals and the loaded count with the sort", async () => {
    mockAttribution(() =>
      Promise.resolve(
        response({
          items: [row("acme"), row("globex")],
          next_cursor: "next",
          totals: { ...response().totals, traces: 14200, cost_usd: "100.00000000" },
        }),
      ),
    );
    renderApp(ROLLUP);

    const view = await table();
    const total = view.getByRole("row", { name: /Total/ });
    expect(within(total).getByText("14,200")).toBeInTheDocument();
    expect(within(total).getByText("$100.00")).toBeInTheDocument();
    expect(
      screen.getByText(
        /^Top 2 values by cost\. The total covers every value, including those not loaded yet\.$/,
      ),
    ).toBeInTheDocument();
  });
});
