import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AddFilterPopover } from "./add-filter-popover";

async function openWith(metadataKey: string | undefined) {
  const onApply = vi.fn();
  render(
    <AddFilterPopover
      search={{ metadata_key: metadataKey }}
      metadataKeys={["customer_id", "region"]}
      onApply={onApply}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "Add filter" }));
  return onApply;
}

describe("AddFilterPopover's metadata entry", () => {
  it("applies the key and the value exactly as typed", async () => {
    const onApply = await openWith("customer_id");
    await userEvent.type(screen.getByRole("textbox", { name: "Metadata value" }), " acme");
    await userEvent.click(screen.getByRole("button", { name: "Apply filters" }));

    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({ metadata_key: "customer_id", metadata_value: " acme" }),
    );
  });

  it("applies the key alone when the value is empty, the traces without it", async () => {
    const onApply = await openWith("customer_id");
    await userEvent.click(screen.getByRole("button", { name: "Apply filters" }));

    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({ metadata_key: "customer_id", metadata_value: "" }),
    );
  });

  it("refuses a value over 256 code points", async () => {
    const onApply = await openWith("customer_id");
    const input = screen.getByRole("textbox", { name: "Metadata value" });
    await userEvent.click(input);
    await userEvent.paste("é".repeat(257));
    await userEvent.click(screen.getByRole("button", { name: "Apply filters" }));

    expect(screen.getByText("A value has at most 256 characters.")).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(onApply).not.toHaveBeenCalled();
  });

  it("keeps the value input off until a key is picked", async () => {
    await openWith(undefined);
    expect(screen.getByRole("textbox", { name: "Metadata value" })).toBeDisabled();
  });
});
