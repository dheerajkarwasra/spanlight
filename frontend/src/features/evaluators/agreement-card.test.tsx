import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError, evaluatorsApi, type Agreement } from "@/lib/api";

import { AgreementCard } from "./agreement-card";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to }: { children: ReactNode; to: string }) => <a href={to}>{children}</a>,
}));

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

const OK: Agreement = {
  evaluator_id: "ev_1",
  score_name: "helpfulness",
  data_type: "numeric",
  status: "ok",
  pairs: 48,
  min_pairs: 30,
  capped: false,
  kappa: 0.72,
  kappa_kind: "quadratic_weighted",
  f1: 0.81,
  pass_threshold: 0.5,
  labels: ["0–0.5", "0.5–1"],
  matrix: [
    [20, 4],
    [3, 21],
  ],
  flagged: false,
};

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <AgreementCard evaluatorId="ev_1" />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("AgreementCard", () => {
  it("shows a loading skeleton", () => {
    vi.spyOn(evaluatorsApi, "agreement").mockReturnValue(new Promise(() => undefined));
    renderCard();
    expect(screen.getByRole("status", { name: "Loading agreement" })).toBeInTheDocument();
  });

  it("offers a retry when the request fails", async () => {
    const spy = vi.spyOn(evaluatorsApi, "agreement").mockRejectedValue(new ApiError(500, {}));
    renderCard();
    await userEvent.click(await screen.findByRole("button", { name: /try again|retry/i }));
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("says how many pairs are missing and links to the Review hub", async () => {
    vi.spyOn(evaluatorsApi, "agreement").mockResolvedValue({
      ...OK,
      status: "not_enough_pairs",
      pairs: 12,
      kappa: null,
      f1: null,
      matrix: [],
    });
    renderCard();
    expect(await screen.findByText("Not enough paired traces yet · 12 of 30")).toBeInTheDocument();
    expect(
      screen.getByText(
        "κ and F1 show once 30 traces have both a review score and a score from this judge.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review traces" })).toHaveAttribute(
      "href",
      "/$orgId/$projectId/queues",
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("explains an undefined κ", async () => {
    vi.spyOn(evaluatorsApi, "agreement").mockResolvedValue({
      ...OK,
      status: "undefined",
      pairs: 41,
      kappa: null,
    });
    renderCard();
    expect(
      await screen.findByText("All pairs are in one class, so κ can't be computed."),
    ).toBeInTheDocument();
  });

  it("shows κ with its kind, F1 and the confusion matrix", async () => {
    vi.spyOn(evaluatorsApi, "agreement").mockResolvedValue(OK);
    renderCard();
    expect(await screen.findByText("Quadratic-weighted κ")).toBeInTheDocument();
    expect(screen.getByText("0.72")).toBeInTheDocument();
    expect(screen.getByText("Moderate agreement")).toBeInTheDocument();
    expect(screen.getByText("0.81")).toBeInTheDocument();
    expect(screen.getByText(/Pass = 0.5 or more/)).toBeInTheDocument();

    const region = screen.getByRole("region", { name: "Confusion matrix" });
    expect(region).toHaveAttribute("tabindex", "0");
    const table = within(region).getByRole("table");
    expect(within(table).getAllByRole("columnheader")).toHaveLength(3);
    expect(within(table).getByRole("rowheader", { name: "0–0.5" })).toHaveAttribute("scope", "row");
    expect(screen.queryByText(/Weak agreement \(κ below 0.60\)/)).not.toBeInTheDocument();
  });

  it("names Cohen's κ and warns when agreement is weak", async () => {
    vi.spyOn(evaluatorsApi, "agreement").mockResolvedValue({
      ...OK,
      data_type: "boolean",
      kappa: 0.41,
      kappa_kind: "cohen",
      pass_threshold: null,
      labels: ["No", "Yes"],
      flagged: true,
    });
    renderCard();
    expect(await screen.findByText("Cohen’s κ")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Weak agreement (κ below 0.60). Check the judge's prompt against reviewed traces before relying on its mean.",
      ),
    ).toBeInTheDocument();
  });
});
