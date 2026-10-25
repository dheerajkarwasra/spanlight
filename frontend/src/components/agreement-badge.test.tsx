import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { AgreementSummary } from "@/lib/api";

import { AgreementBadge } from "./agreement-badge";

const OK: AgreementSummary = {
  evaluator_id: "ev_1",
  score_name: "helpfulness",
  status: "ok",
  pairs: 48,
  kappa: 0.72,
  flagged: false,
};

function renderBadge(summary: AgreementSummary | undefined, inLink = false) {
  return render(
    <TooltipProvider>
      <AgreementBadge summary={summary} inLink={inLink} />
    </TooltipProvider>,
  );
}

describe("AgreementBadge", () => {
  it("is focusable, named with the full sentence and explains κ on focus", async () => {
    renderBadge(OK);
    const badge = screen.getByLabelText(
      "Judge agreement with reviewers: kappa 0.72 over 48 traces",
    );
    expect(badge).toHaveTextContent("κ 0.72 · 48 pairs");

    await userEvent.tab();
    expect(badge).toHaveFocus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Kappa: 1 is perfect agreement with reviewers, 0 is no better than chance.",
    );
  });

  it("carries an icon and the word weak when flagged", () => {
    renderBadge({ ...OK, kappa: 0.41, flagged: true });
    const badge = screen.getByText("κ 0.41 · weak").closest("span[tabindex]");
    expect(badge?.querySelector("svg")).not.toBeNull();
  });

  it("is plain text with the sentence for screen readers inside a link", () => {
    renderBadge(OK, true);
    expect(
      screen.getByText("Judge agreement with reviewers: kappa 0.72 over 48 traces"),
    ).toHaveClass("sr-only");
    expect(document.querySelector("[tabindex]")).toBeNull();
  });

  it("renders nothing without a summary or for an unsupported score", () => {
    const { container, rerender } = renderBadge(undefined);
    expect(container).toBeEmptyDOMElement();
    rerender(
      <TooltipProvider>
        <AgreementBadge summary={{ ...OK, status: "unsupported", kappa: null }} />
      </TooltipProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
