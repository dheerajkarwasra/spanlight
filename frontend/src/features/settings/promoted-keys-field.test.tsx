import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useForm, useWatch } from "react-hook-form";
import { describe, expect, it, vi } from "vitest";

import type { ProjectSettingsValues } from "./project-settings-schema";
import { PromotedKeysField } from "./promoted-keys-field";

vi.mock("@/features/shell", () => ({
  useFilterOptionsQuery: () => ({ data: { metadata_keys: ["plan", "plan_tier", "feature"] } }),
}));

function Harness({ keys, canEdit }: { keys: string[]; canEdit: boolean }) {
  const form = useForm<ProjectSettingsValues>({
    defaultValues: { promoted_metadata_keys: keys },
  });
  const keysNow = useWatch({ control: form.control, name: "promoted_metadata_keys" });
  return (
    <>
      <PromotedKeysField control={form.control} canEdit={canEdit} />
      <output data-testid="value">{keysNow.join(",")}</output>
    </>
  );
}

describe("PromotedKeysField", () => {
  it("shows a viewer the keys read-only", () => {
    render(<Harness keys={["customer_id", "feature"]} canEdit={false} />);
    expect(screen.getByRole("list", { name: "Promoted keys" }).textContent).toBe(
      "customer_idfeature",
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    // The page-level view-only banner explains it; the field adds no line of its own.
    expect(screen.queryByText(/Only admins and owners/)).toBeNull();
    expect(screen.getByText("2 of 5")).toBeTruthy();
  });

  it("checks a typed key inline and adds a valid one on Enter", async () => {
    const user = userEvent.setup();
    render(<Harness keys={["customer_id", "feature"]} canEdit />);
    const input = screen.getByRole("combobox", { name: "Promoted keys" });
    expect(input).toHaveAccessibleDescription(/^2 of 5 Promoting a key/);

    await user.type(input, "Customer ID");
    expect(
      screen.getByText(
        "Use lower-case letters, digits, dots, dashes or underscores, up to 64 characters.",
      ),
    ).toBeTruthy();
    await user.clear(input);
    await user.type(input, "feature");
    expect(screen.getByText("feature is already promoted.")).toBeTruthy();
    await user.clear(input);
    await user.type(input, "team{Enter}");
    expect(screen.getByTestId("value").textContent).toBe("customer_id,feature,team");
  });

  it("removes a key and hides the input once five are promoted", async () => {
    const user = userEvent.setup();
    render(<Harness keys={["a", "b", "c", "d", "e"]} canEdit />);
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByText("All 5 slots are used. Remove a key to promote another.")).toBeTruthy();
    expect(screen.getByRole("list", { name: "Promoted keys" }).children).toHaveLength(5);
    await user.click(screen.getByRole("button", { name: "Remove c" }));
    expect(screen.getByTestId("value").textContent).toBe("a,b,d,e");
    // The removed chip's button is gone: focus moves to the text box that mounts in its place.
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByRole("combobox", { name: "Promoted keys" }));
    });
  });
});
