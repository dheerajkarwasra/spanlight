import { render as renderPlain, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { ReleasePicker } from "./release-picker";

function render(ui: ReactElement) {
  return renderPlain(<TooltipProvider>{ui}</TooltipProvider>);
}

describe("ReleasePicker without a release list", () => {
  // Finding T7-03: the selects shimmered forever after the list failed.
  it("disables both selects with the error placeholder once the list failed", () => {
    render(
      <ReleasePicker
        releases={undefined}
        failed
        a={undefined}
        b={undefined}
        unavailable={false}
        onCompare={() => undefined}
      />,
    );

    for (const name of ["Baseline (A)", "Candidate (B)"]) {
      const select = screen.getByRole("combobox", { name });
      expect(select).toBeDisabled();
      expect(select).toHaveTextContent("Couldn't load releases");
    }
  });

  it("disables both selects when the window is too large", () => {
    render(
      <ReleasePicker
        releases={undefined}
        failed={false}
        a={undefined}
        b={undefined}
        unavailable
        onCompare={() => undefined}
      />,
    );

    for (const name of ["Baseline (A)", "Candidate (B)"]) {
      const select = screen.getByRole("combobox", { name });
      expect(select).toBeDisabled();
      expect(select).toHaveTextContent("Unavailable for this range");
    }
  });

  it("shows no select while the list loads", () => {
    render(
      <ReleasePicker
        releases={undefined}
        failed={false}
        a={undefined}
        b={undefined}
        unavailable={false}
        onCompare={() => undefined}
      />,
    );

    expect(screen.queryByRole("combobox")).toBeNull();
  });
});
