import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ReasoningDisclosure } from "./reasoning-disclosure";

const COMMENT = "The answer cites KB-112 but promises express delivery without checking stock.";

describe("ReasoningDisclosure", () => {
  it("is collapsed by default and expands to show the judge's reasoning", async () => {
    render(<ReasoningDisclosure text={COMMENT} scoreName="relevance" />);
    const button = screen.getByRole("button", { name: "Reasoning for relevance" });

    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(COMMENT)).not.toBeInTheDocument();

    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(COMMENT)).toBeVisible();

    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");
  });
});
