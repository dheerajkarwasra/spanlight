import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { SetupCategoriesStep } from "./setup-categories-step";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ hash, children }: { hash?: string; children: ReactNode }) => (
    <a href={`/org_1/proj_1/settings/project#${hash ?? ""}`}>{children}</a>
  ),
}));

vi.mock("@/features/shell", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

describe("SetupCategoriesStep", () => {
  it("links to the score settings and gives each remove button a 24 px target", () => {
    render(<SetupCategoriesStep categories={["none", "wrong_tool"]} onChange={() => undefined} />);
    expect(screen.getByRole("link", { name: "score configs" })).toHaveAttribute(
      "href",
      "/org_1/proj_1/settings/project#score-configs",
    );
    expect(screen.getByRole("link", { name: "failure categories" })).toHaveAttribute(
      "href",
      "/org_1/proj_1/settings/project#failure-categories",
    );
    expect(screen.getByRole("button", { name: "Remove wrong_tool" })).toHaveClass(
      "size-6",
      "-my-1",
    );
  });
});
