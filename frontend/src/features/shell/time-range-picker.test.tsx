import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { resolveRange, type RangeValue } from "@/lib/time-range";

import { EnvironmentFilter } from "./environment-filter";
import { TimeRangePicker } from "./time-range-picker";
import { dataFiltersFor } from "./use-data-filters";

const navigate = vi.hoisted(() => vi.fn());
const search = vi.hoisted<{ range: RangeValue }>(() => ({ range: "24h" }));

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useNavigate: () => navigate,
}));
vi.mock("./project-context", () => ({
  useProjectFilters: () => ({
    range: resolveRange({ range: search.range }),
    environment: undefined,
  }),
  useFilterOptionsQuery: () => ({ data: undefined }),
}));

function renderPicker(path: string) {
  render(
    <TooltipProvider>
      <TimeRangePicker options={dataFiltersFor(path)} />
    </TooltipProvider>,
  );
}

describe("TimeRangePicker", () => {
  beforeEach(() => {
    navigate.mockClear();
  });

  it("disables the whole control on a page that can't honour it, and says why", () => {
    search.range = "24h";
    renderPicker("/o/p/doctor");

    for (const radio of screen.getAllByRole("radio")) {
      expect(radio).toBeDisabled();
    }
    expect(screen.getByRole("button", { name: "Custom range" })).toBeDisabled();
    const group = screen.getByRole("group", { name: "Time range" });
    expect(group).toHaveAttribute("tabindex", "0");
    expect(group).toHaveAccessibleDescription(
      "Findings aren't filtered by time; each one has its own window.",
    );
  });

  it("reads a shared 30d range as 7d on the gateway overview without rewriting the URL", () => {
    search.range = "30d";
    renderPicker("/o/p/gateway");

    expect(screen.getByRole("radio", { name: /^7d/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /^30d/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /^1h/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Custom range" })).toBeDisabled();
    expect(screen.getByRole("radiogroup", { name: "Time range" })).toHaveAccessibleDescription(
      "The gateway overview covers at most 7 days.",
    );
    expect(navigate).not.toHaveBeenCalled();
  });

  it("disables the environment pill on a page that can't honour it, and says why", () => {
    render(
      <TooltipProvider>
        <EnvironmentFilter disabledReason={dataFiltersFor("/o/p/doctor").environmentDisabled} />
      </TooltipProvider>,
    );

    expect(screen.getByRole("combobox", { name: "Environment" })).toBeDisabled();
    const group = screen.getByRole("group", { name: "Environment" });
    expect(group).toHaveAttribute("tabindex", "0");
    expect(group).toHaveAccessibleDescription("Findings cover every environment.");
  });
});
