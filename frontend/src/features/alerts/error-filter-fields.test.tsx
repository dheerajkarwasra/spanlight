import { render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { describe, expect, it } from "vitest";

import { ErrorFilterFields } from "./error-filter-fields";
import { EMPTY_RULE_VALUES, type RuleFormValues } from "./rule-form-schema";

const REFUSED = "filters.error_class: applies to error_rate and error_calls";

function Harness({ serverError }: { serverError?: string }) {
  const form = useForm<RuleFormValues>({
    defaultValues: { ...EMPTY_RULE_VALUES, errorClass: "rate_limit" },
  });
  useEffect(() => {
    if (serverError) {
      form.setError("errorClass", { type: "server", message: serverError });
    }
  }, [form, serverError]);
  return <ErrorFilterFields form={form} />;
}

describe("ErrorFilterFields", () => {
  it("names each select by its label", () => {
    render(<Harness />);
    expect(screen.getByRole("combobox", { name: "Error class" })).toHaveTextContent("Rate limited");
    expect(screen.getByRole("combobox", { name: "Rate-limit kind" })).toHaveTextContent("Any kind");
  });

  it("links a refused save's message to the select and marks it invalid", async () => {
    render(<Harness serverError={REFUSED} />);
    const select = screen.getByRole("combobox", { name: "Error class" });
    expect(await screen.findByText(REFUSED)).toBeInTheDocument();
    expect(select).toHaveAttribute("aria-invalid", "true");
    expect(select).toHaveAccessibleDescription(REFUSED);
  });
});
