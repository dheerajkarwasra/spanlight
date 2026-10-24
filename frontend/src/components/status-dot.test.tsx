import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusDot } from "./status-dot";

describe("StatusDot", () => {
  it("renders an X-circle icon for error", () => {
    const { container } = render(<StatusDot state="error" />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg).toHaveClass("lucide-circle-x", "size-3", "text-danger");
  });

  it("renders a triangle icon for warning", () => {
    const { container } = render(<StatusDot state="warning" />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg).toHaveClass("lucide-triangle-alert", "text-warning");
  });

  it("keeps an 8px dot for ok, unset and live", () => {
    for (const state of ["ok", "unset", "live"] as const) {
      const { container, unmount } = render(<StatusDot state={state} />);
      expect(container.querySelector("svg")).toBeNull();
      expect(container.firstElementChild).toHaveClass("size-2");
      unmount();
    }
  });

  it("labelled icon exposes role img and name", () => {
    render(<StatusDot state="error" label="Failed" />);
    expect(screen.getByRole("img", { name: "Failed" })).toBeInTheDocument();
  });

  it("unlabelled icon is hidden from assistive tech", () => {
    const { container } = render(<StatusDot state="warning" />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });
});
