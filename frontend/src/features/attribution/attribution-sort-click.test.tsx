import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { mockSignedIn, renderApp } from "@/test/render-app";

import { mockAttribution, PAGE, project, response } from "./attribution-test-data";

const ROLLUP = `${PAGE}?group_by=metadata&key=customer_id&range=30d`;

describe("Attribution sorting", () => {
  beforeEach(() => {
    mockSignedIn([project(["customer_id"])]);
  });

  it("asks the API for the clicked column and marks it sorted", async () => {
    const read = mockAttribution(() => Promise.resolve(response()));
    renderApp(ROLLUP);

    const view = within(await screen.findByRole("table", { name: "Cost by customer_id" }));
    await userEvent.click(view.getByRole("button", { name: "Sort by LLM calls" }));

    await waitFor(() => {
      expect(read).toHaveBeenLastCalledWith("proj_1", expect.objectContaining({ sort: "calls" }));
    });
    expect(view.getByRole("columnheader", { name: "LLM calls" })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
  });

  it("sorts from the phone sheet", async () => {
    const read = mockAttribution(() => Promise.resolve(response()));
    renderApp(ROLLUP);

    await userEvent.click(await screen.findByRole("button", { name: "Sort: Cost" }));
    await userEvent.click(await screen.findByRole("button", { name: "Errors" }));

    await waitFor(() => {
      expect(read).toHaveBeenLastCalledWith("proj_1", expect.objectContaining({ sort: "errors" }));
    });
    expect(await screen.findByRole("button", { name: "Sort: Errors" })).toBeInTheDocument();
  });
});
