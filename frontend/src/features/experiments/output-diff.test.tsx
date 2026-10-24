import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { OutputDiff } from "./output-diff";

const NAMES = { a: "Run #2", b: "Run #3" };

describe("OutputDiff", () => {
  it("opens an aligned diff with − and + gutters that are read out as Removed and Added", async () => {
    render(<OutputDiff a={"Hello\nClick Export as CSV."} b={"Hello\nChoose PDF."} names={NAMES} />);
    const button = screen.getByRole("button", { name: "Show diff" });
    expect(button).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(button).toHaveAccessibleName("Hide diff");
    const diff = document.getElementById(button.getAttribute("aria-controls") ?? "");
    expect(diff).not.toBeNull();
    expect(screen.getByText("A · Run #2")).toBeInTheDocument();
    expect(screen.getByText("B · Run #3")).toBeInTheDocument();
    expect(screen.getByText("−")).toHaveAttribute("aria-hidden");
    expect(screen.getByText("+")).toHaveAttribute("aria-hidden");
    expect(screen.getByText("Removed:")).toHaveClass("sr-only");
    expect(screen.getByText("Added:")).toHaveClass("sr-only");
  });

  it("names a long unchanged run by its count", async () => {
    const shared = Array.from({ length: 14 }, (_, index) => `line ${String(index)}`);
    render(
      <OutputDiff
        a={[...shared, "old"].join("\n")}
        b={[...shared, "new"].join("\n")}
        names={NAMES}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Show diff" }));
    expect(screen.getAllByText("8 unchanged lines").length).toBeGreaterThan(0);
  });

  it("says Same output instead of a diff when both answers are equal", () => {
    render(<OutputDiff a="Same." b="Same." names={NAMES} />);
    expect(screen.getByText("Same output")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show diff" })).not.toBeInTheDocument();
  });

  it("renders nothing when either answer is missing", () => {
    const { container } = render(<OutputDiff a={null} b="An answer." names={NAMES} />);
    expect(container).toBeEmptyDOMElement();
  });
});
