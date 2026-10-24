import { render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import type { TraceDetail } from "@/lib/api";

import { TraceAttributeChips } from "./trace-attribute-chips";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, className, title }: ComponentProps<"a">) => (
    <a href="/target" className={className} title={title}>
      {children}
    </a>
  ),
}));

vi.mock("@/features/shell", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

const TRACE = {
  release: "v1.4.2",
  external_user_id: "u_8f21",
  session_id: "s_42",
  tags: ["beta"],
  spans: [],
} as Partial<TraceDetail> as TraceDetail;

describe("TraceAttributeChips target size", () => {
  it("makes the chips 28 px and their links at least 24 px tall", () => {
    render(<TraceAttributeChips trace={TRACE} />);

    for (const name of ["u_8f21", "s_42"]) {
      expect(screen.getByRole("link", { name })).toHaveClass("min-h-6");
    }
    for (const label of ["Release", "User", "Session", "Tags"]) {
      expect(screen.getByText(label).parentElement).toHaveClass("min-h-7");
    }
  });
});
