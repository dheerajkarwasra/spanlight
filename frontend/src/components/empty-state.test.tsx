import { render, screen } from "@testing-library/react";
import { FileText } from "lucide-react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "./empty-state";

describe("EmptyState", () => {
  it("renders the setup slot under the actions, behind a hairline", () => {
    render(
      <EmptyState
        icon={FileText}
        title="No prompts yet"
        description="Create one."
        action={<button type="button">Create prompt</button>}
        setup={<p>Setup here</p>}
      />,
    );
    const action = screen.getByRole("button", { name: "Create prompt" });
    const slot = screen.getByText("Setup here").closest("div");
    expect(slot).toHaveClass("border-t");
    expect(slot && action.compareDocumentPosition(slot)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("has no hairline without a setup slot", () => {
    const { container } = render(
      <EmptyState icon={FileText} title="No prompts yet" description="Create one." />,
    );
    expect(container.querySelector(".border-t")).toBeNull();
  });
});
