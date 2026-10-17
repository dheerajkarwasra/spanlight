import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WindowPicker } from "./window-picker-popover";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-16T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("WindowPicker", () => {
  it("announces a refused window and moves focus to the last day", async () => {
    const onChange = vi.fn();
    render(
      <WindowPicker
        value={{ from: "2026-10-02T00:00:00Z", to: "2026-10-16T00:00:00Z" }}
        onChange={onChange}
      />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Window, Oct 2 to Oct 15, 14 days, UTC" }),
    );
    fireEvent.change(await screen.findByLabelText("First day"), {
      target: { value: "2026-08-01" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Apply window" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The window may span at most 31 whole UTC days.",
    );
    await waitFor(() => {
      expect(screen.getByLabelText("Last day")).toHaveFocus();
    });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("applies a valid window as its first and last day", async () => {
    const onChange = vi.fn();
    render(
      <WindowPicker
        value={{ from: "2026-10-02T00:00:00Z", to: "2026-10-16T00:00:00Z" }}
        onChange={onChange}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /^Window/ }));
    fireEvent.change(await screen.findByLabelText("First day"), {
      target: { value: "2026-10-10" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Apply window" }));

    expect(onChange).toHaveBeenCalledWith({ first: "2026-10-10", last: "2026-10-15" });
  });
});
