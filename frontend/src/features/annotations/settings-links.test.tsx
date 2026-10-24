import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { QueuesPage } from "./queues-page";
import { ScoreConfigPicker } from "./score-config-picker";
import { ScoresPanel } from "./scores-panel";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ hash, children }: { hash?: string; children: ReactNode }) => (
    <a href={`/org_1/proj_1/settings/project#${hash ?? ""}`}>{children}</a>
  ),
}));

vi.mock("@/features/shell", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
  usePermission: () => false,
}));
vi.mock("@/features/review", () => ({ ReviewPolicyCard: () => null }));
vi.mock("@/features/auth", () => ({ LiveDemoButton: () => null }));
vi.mock("./annotation-queries", () => ({
  useQueuesQuery: () => ({ isPending: true, data: undefined }),
  useScoreConfigsQuery: () => ({ data: undefined }),
}));

function expectSettingsLinks() {
  expect(screen.getByRole("link", { name: "score configs" })).toHaveAttribute(
    "href",
    "/org_1/proj_1/settings/project#score-configs",
  );
  expect(screen.getByRole("link", { name: "failure categories" })).toHaveAttribute(
    "href",
    "/org_1/proj_1/settings/project#failure-categories",
  );
}

describe("Evaluate → Settings links", () => {
  it("names the score settings in the Review page description", () => {
    render(<QueuesPage />);
    expect(document.body).toHaveTextContent(
      "Scores use the score configs and failure categories in Settings › Project.",
    );
    expectSettingsLinks();
  });

  it("links Settings in the picker's no-configs line to the score configs", () => {
    render(<ScoreConfigPicker configs={[]} picked={[]} onChange={() => undefined} />);
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute(
      "href",
      "/org_1/proj_1/settings/project#score-configs",
    );
  });
});

describe("ScoresPanel", () => {
  it("keeps focused fields clear of the sticky action stack", () => {
    render(
      <ScoresPanel
        configs={[]}
        instructions={null}
        draft={{
          itemId: "item_1",
          values: {},
          comment: "",
          focused: null,
          setValue: () => undefined,
          setComment: () => undefined,
          setFocused: () => undefined,
        }}
        work={{
          notice: null,
          busy: null,
          onSubmit: () => undefined,
          onSkip: () => undefined,
          onRelease: () => undefined,
          moreActions: null,
        }}
        lastItem={null}
      />,
    );
    const comment = screen.getByRole("textbox", { name: "Comment (optional)" });
    expect(comment.closest('[class*="scroll-mb-52"]')).toHaveClass("max-lg:[&_*]:scroll-mb-52");
  });
});
