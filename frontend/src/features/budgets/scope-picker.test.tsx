import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useForm } from "react-hook-form";
import { describe, expect, it, vi } from "vitest";

import { EMPTY_BUDGET_VALUES, type BudgetFormValues } from "./budget-schema";
import { ScopePicker } from "./scope-picker";

vi.mock("./budgets-queries", () => ({
  useGatewayKeysQuery: () => ({ data: [], isPending: false, isError: false }),
  useScopeSuggestions: () => ({
    metadataKeys: [
      { value: "customer_id", promoted: true },
      { value: "region", promoted: false },
    ],
    traceNames: ["summarize-thread", "summarize-ticket", "summary_digest", "classify"],
    promotedKeys: ["customer_id"],
  }),
}));

function Harness({ initial }: { initial: Partial<BudgetFormValues> }) {
  const form = useForm<BudgetFormValues>({
    defaultValues: { ...EMPTY_BUDGET_VALUES, ...initial },
  });
  return <ScopePicker form={form} projectName="Support Copilot" />;
}

describe("ScopePicker", () => {
  it("offers six scopes", () => {
    render(<Harness initial={{}} />);
    expect(screen.getAllByRole("radio").map((radio) => radio.textContent)).toEqual([
      "Project",
      "Gateway key",
      "End user",
      "Model",
      "Metadata",
      "Trace name",
    ]);
  });

  it("suggests trace names seen in the last 7 days that match what is typed", async () => {
    const user = userEvent.setup();
    render(<Harness initial={{}} />);
    await user.click(screen.getByRole("radio", { name: "Trace name" }));
    await user.type(screen.getByRole("combobox", { name: "Trace name" }), "summ");

    expect(screen.getByText("Seen in the last 7 days")).toBeInTheDocument();
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
      "summarize-thread",
      "summarize-ticket",
      "summary_digest",
    ]);
    await user.click(screen.getByRole("option", { name: "summarize-ticket" }));
    expect(screen.getByRole("combobox", { name: "Trace name" })).toHaveValue("summarize-ticket");
  });

  it("keeps the typed text on Enter until an arrow key moves to a suggestion", async () => {
    const user = userEvent.setup();
    render(<Harness initial={{ scope: "trace_name" }} />);
    const field = screen.getByRole("combobox", { name: "Trace name" });
    await user.type(field, "summarize");
    expect(screen.getByRole("option", { name: "summarize-thread" })).toBeInTheDocument();
    expect(field).not.toHaveAttribute("aria-activedescendant");

    await user.keyboard("{Enter}");
    expect(field).toHaveValue("summarize");

    await user.click(field);
    await user.keyboard("{ArrowDown}{Enter}");
    expect(field).toHaveValue("summarize-thread");
  });

  it("marks promoted keys and tips promotion for a key that is not promoted", async () => {
    const user = userEvent.setup();
    render(<Harness initial={{ scope: "metadata", metadataKey: "customer_id" }} />);
    expect(screen.getByText("promoted")).toBeInTheDocument();
    expect(screen.queryByText(/so Attribution shows the same spend/)).toBeNull();

    const key = screen.getByRole("combobox", { name: "Key" });
    await user.clear(key);
    await user.type(key, "region");
    expect(screen.getByText(/so Attribution shows the same spend/)).toBeInTheDocument();
  });
});
