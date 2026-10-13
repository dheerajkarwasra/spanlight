import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FilterSelectPill } from "./audit-filter-pill";

const OPTIONS = [{ value: "api_key.created", label: "API key created" }];

describe("FilterSelectPill", () => {
  it("names the combobox by its label and the value it shows", () => {
    // Finding T7-01: axe "button-name"; the trigger took no name from its content.
    render(
      <FilterSelectPill
        label="Action"
        allLabel="All actions"
        value={undefined}
        options={OPTIONS}
        onChange={() => undefined}
      />,
    );

    expect(screen.getByRole("combobox", { name: "Action All actions" })).toBeInTheDocument();
  });

  it("follows the chosen value in its name", () => {
    render(
      <FilterSelectPill
        label="Action"
        allLabel="All actions"
        value="api_key.created"
        options={OPTIONS}
        onChange={() => undefined}
      />,
    );

    expect(screen.getByRole("combobox", { name: "Action API key created" })).toBeInTheDocument();
  });
});
