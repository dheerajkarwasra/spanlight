import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ErrorState } from "./error-state";

describe("ErrorState", () => {
  it("shows a fixed description instead of the error's message", () => {
    render(<ErrorState error={new Error("boom")} description="The request failed." />);
    expect(screen.getByRole("alert")).toHaveTextContent("The request failed.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("boom");
  });

  it("disables a retry that is already in flight", () => {
    render(<ErrorState error={new Error("boom")} onRetry={vi.fn()} retrying />);
    const button = screen.getByRole("button", { name: "Try again" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });
});
