import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { JsonViewer } from "./json-viewer";

describe("JsonViewer", () => {
  it("renders primitives and keys", () => {
    render(<JsonViewer value={{ model: "gpt-4o-mini", tokens: 12, stream: false, stop: null }} />);
    expect(screen.getByText('"model"')).toBeInTheDocument();
    expect(screen.getByText('"gpt-4o-mini"')).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("false")).toBeInTheDocument();
    expect(screen.getByText("null")).toBeInTheDocument();
  });

  it("collapses deep nodes and expands them on click", async () => {
    const user = userEvent.setup();
    render(<JsonViewer value={{ a: { b: { c: "deep" } } }} defaultExpandDepth={2} />);

    expect(screen.queryByText('"deep"')).not.toBeInTheDocument();
    const collapsed = screen.getByRole("button", { expanded: false });
    await user.click(collapsed);
    expect(screen.getByText('"deep"')).toBeInTheDocument();
  });

  it("renders a long or multi-line string as wrapped prose with its newlines", () => {
    const system = "You are Support Copilot for Acme.\nNever promise refunds.";
    render(<JsonViewer value={{ model: "claude-sonnet-4-5", system }} />);

    const prose = screen.getByText(/You are Support Copilot for Acme\./);
    expect(prose.textContent).toBe(system);
    expect(prose).toHaveClass("whitespace-pre-wrap", "font-sans");
    expect(screen.queryByText(JSON.stringify(system))).not.toBeInTheDocument();
    // Short strings stay JSON literals.
    expect(screen.getByText('"claude-sonnet-4-5"')).toBeInTheDocument();
  });

  it("treats a string over 200 characters as prose", () => {
    const long = "word ".repeat(41).trim();
    render(<JsonViewer value={{ long }} />);
    expect(screen.getByText(long)).toHaveClass("font-sans");
  });

  it("restores the escaped JSON rendering with View as JSON", async () => {
    const user = userEvent.setup();
    const system = "Line one.\nLine two.";
    render(<JsonViewer value={{ system }} />);

    const toggle = screen.getByRole("button", { name: "View as JSON" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(JSON.stringify(system))).toBeInTheDocument();
  });

  it("offers no View as JSON toggle when nothing renders as prose", () => {
    render(<JsonViewer value={{ model: "gpt-4o-mini" }} />);
    expect(screen.queryByRole("button", { name: "View as JSON" })).not.toBeInTheDocument();
  });

  it("keeps every string a JSON literal with prose off", () => {
    const system = "Line one.\nLine two.";
    render(<JsonViewer value={{ system }} prose={false} />);
    expect(screen.getByText(JSON.stringify(system))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "View as JSON" })).not.toBeInTheDocument();
  });
});
