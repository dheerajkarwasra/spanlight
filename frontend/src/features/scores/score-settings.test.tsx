import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { scoresApi } from "@/lib/api";

import { FailureCategoriesRow } from "./failure-categories-row";
import { ScoreConfigsCard } from "./score-configs-card";
import { ScoresCell } from "./scores-cell";
import { ScoreSettingsLinks } from "./score-settings-links";

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    to,
    params,
    hash,
    children,
  }: {
    to: string;
    params: Record<string, string>;
    hash?: string;
    children: ReactNode;
  }) => (
    <a
      href={`${Object.entries(params).reduce((path, [key, value]) => path.replace(`$${key}`, value), to)}#${hash ?? ""}`}
    >
      {children}
    </a>
  ),
}));

vi.mock("@/features/shell", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
  usePermission: () => false,
}));

vi.mock("@/features/auth", () => ({ LiveDemoButton: () => null }));

describe("score settings anchors and links", () => {
  it("anchors the failure categories row at #failure-categories", () => {
    const { container } = render(<FailureCategoriesRow configs={[]} canWrite={false} />);
    expect(container.querySelector("#failure-categories")).toHaveTextContent("Failure categories");
  });

  it("links score configs and failure categories to their Settings › Project anchors", () => {
    const { container } = render(<ScoreSettingsLinks />);
    expect(container).toHaveTextContent(
      "Scores use the score configs and failure categories in Settings › Project.",
    );
    expect(screen.getByRole("link", { name: "score configs" })).toHaveAttribute(
      "href",
      "/org_1/proj_1/settings/project#score-configs",
    );
    expect(screen.getByRole("link", { name: "failure categories" })).toHaveAttribute(
      "href",
      "/org_1/proj_1/settings/project#failure-categories",
    );
  });
});

// Final review L3: a link to Settings › Project#failure-categories landed at the top of the page,
// because the row only exists once the configs load.
describe("ScoreConfigsCard hash link", () => {
  it("scrolls to the anchor named in the hash once the configs load", async () => {
    window.location.hash = "#failure-categories";
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    vi.spyOn(scoresApi, "configs").mockResolvedValue([]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ScoreConfigsCard />
        </TooltipProvider>
      </QueryClientProvider>,
    );

    await screen.findByText("No score configs yet");
    const row = container.querySelector("#failure-categories");
    expect(row).toHaveClass("scroll-mt-6");
    expect(scroll.mock.contexts).toEqual([row]);
    window.location.hash = "";
    scroll.mockRestore();
  });
});

describe("ScoresCell", () => {
  it("gives the +N badge a 24 px hit area", () => {
    const score = (name: string) => ({ name, value: 1, string_value: null });
    render(
      <TooltipProvider>
        <ScoresCell scores={[score("a"), score("b")]} typeOf={() => null} />
      </TooltipProvider>,
    );
    expect(screen.getByText("+1")).toHaveClass("relative", "after:absolute", "after:-inset-0.5");
  });
});
