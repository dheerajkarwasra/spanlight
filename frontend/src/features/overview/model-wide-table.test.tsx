import { render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { ModelMetrics } from "@/lib/api";

import { ModelsTable } from "./model-wide-table";
import { NO_MODEL_USAGE_REASON } from "./models";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, className, title }: ComponentProps<"a">) => (
    <a href="/target" className={className} title={title}>
      {children}
    </a>
  ),
}));

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

const MODEL: ModelMetrics = {
  provider: "openai",
  model: "gpt-4o-mini",
  calls: 3,
  errors: 3,
  p50_ms: 400,
  p95_ms: 900,
  input_tokens: null,
  output_tokens: null,
  cost_usd: null,
  approximate: false,
  aggregate_status: "complete",
  oldest_pending_at: null,
};

describe("ModelsTable", () => {
  it("shows a model whose calls reported no usage as unknown tokens, never as 0", () => {
    render(
      <TooltipProvider>
        <ModelsTable models={[MODEL]} />
      </TooltipProvider>,
    );
    expect(screen.getAllByLabelText(`Unknown: ${NO_MODEL_USAGE_REASON}`)).toHaveLength(2);
  });
});
