import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { SegmentedControl } from "./segmented-control";

const OPTIONS = [
  { value: "1h", label: "1h", srLabel: "last hour" },
  { value: "24h", label: "24h", srLabel: "last 24 hours" },
] as const;

function Harness({ onChange }: { onChange: (value: string) => void }) {
  const [value, setValue] = useState<"1h" | "24h">("24h");
  return (
    <SegmentedControl
      aria-label="Time range"
      value={value}
      options={OPTIONS}
      onValueChange={(next) => {
        setValue(next);
        onChange(next);
      }}
    />
  );
}

describe("SegmentedControl", () => {
  it("is a named radio group whose names start with the visible label", () => {
    render(<Harness onChange={vi.fn()} />);
    expect(screen.getByRole("radiogroup", { name: "Time range" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "24h, last 24 hours" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "1h, last hour" })).not.toBeChecked();
  });

  it("reports the picked value", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await user.click(screen.getByRole("radio", { name: /^1h/ }));
    expect(onChange).toHaveBeenCalledWith("1h");
    expect(screen.getByRole("radio", { name: /^1h/ })).toBeChecked();
  });

  it("rings the selected surface pill in dark mode, where its fill matches the track", () => {
    render(
      <SegmentedControl
        aria-label="Group by"
        tone="surface"
        value="24h"
        options={OPTIONS}
        onValueChange={vi.fn()}
      />,
    );
    const selected = screen.getByRole("radio", { name: /^24h/ });
    expect(selected).toHaveAttribute("data-state", "checked");
    // Ring classes are state-gated, so only the checked item shows them.
    // shadow-none: the dark card shadow is `none`, which would void the composed ring.
    expect(selected).toHaveClass(
      "dark:data-[state=checked]:shadow-none",
      "dark:data-[state=checked]:ring-1",
      "dark:data-[state=checked]:ring-border-strong",
      "dark:data-[state=checked]:ring-inset",
    );
    expect(screen.getByRole("radio", { name: /^1h/ })).toHaveAttribute("data-state", "unchecked");
  });

  it("can show no selection", () => {
    render(
      <SegmentedControl
        aria-label="Range"
        value={null}
        options={OPTIONS}
        onValueChange={vi.fn()}
      />,
    );
    for (const radio of screen.getAllByRole("radio")) {
      expect(radio).not.toBeChecked();
    }
  });
});
