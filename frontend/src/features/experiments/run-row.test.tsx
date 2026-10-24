import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { ExperimentRun } from "@/lib/api";

import { RunRow } from "./run-row";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="/run">{children}</a>,
}));

vi.mock("@/features/shell", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

const RUN: ExperimentRun = {
  id: "run_1",
  experiment_id: "exp_1",
  number: 1,
  target: { kind: "messages", credential_id: "cred_1", model: "gpt-4.1-mini", messages: [] },
  status: "done",
  error: null,
  started_at: "2026-10-21T10:00:00Z",
  finished_at: "2026-10-21T10:01:00Z",
  heartbeat_at: null,
  started_by: null,
  totals: null,
};

describe("RunRow", () => {
  it("puts the compare checkbox inside a 24 px label", () => {
    render(
      <TooltipProvider>
        <ul>
          <RunRow
            run={RUN}
            context={{
              experimentId: "exp_1",
              index: new Map(),
              expectedItems: null,
              score: null,
              liveRun: undefined,
            }}
            ticked={false}
            onTick={() => undefined}
          />
        </ul>
      </TooltipProvider>,
    );
    const label = screen.getByRole("checkbox", { name: "Compare Run #1" }).closest("label");
    expect(label).toHaveClass("size-6");
  });
});
