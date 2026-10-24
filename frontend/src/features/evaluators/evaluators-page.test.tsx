import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { EvaluatorsPage } from "./evaluators-page";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ hash, children }: { hash?: string; children: ReactNode }) => (
    <a href={`/org_1/proj_1/settings/project#${hash ?? ""}`}>{children}</a>
  ),
}));

vi.mock("@/features/shell", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
  usePermission: () => false,
}));
vi.mock("@/features/auth", () => ({ LiveDemoButton: () => null }));
vi.mock("./evaluator-queries", () => ({
  useEvaluatorsQuery: () => ({ isPending: true, data: undefined }),
  useAlertChannelsQuery: () => ({ data: undefined }),
}));

describe("EvaluatorsPage", () => {
  it("names the score settings in the page description", () => {
    render(<EvaluatorsPage />);
    expect(document.body).toHaveTextContent(
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
