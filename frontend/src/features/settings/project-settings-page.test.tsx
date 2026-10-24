import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProjectSettingsPage } from "./project-settings-page";

const permissions = vi.hoisted(() => ({ canEdit: true }));

vi.mock("@/features/shell", () => ({
  useProjectQuery: () => ({ isPending: false, isError: false, data: { id: "proj_1" } }),
  useCurrentOrg: () => ({ id: "org_1", is_demo: false }),
  usePermission: (permission: string) => permission === "project:write" && permissions.canEdit,
}));
vi.mock("@/features/scores", () => ({ ScoreConfigsCard: () => <p>Score configs card</p> }));
vi.mock("./project-settings-form", () => ({
  ProjectSettingsForm: ({ canEdit }: { canEdit: boolean }) =>
    canEdit ? <button type="submit">Save changes</button> : null,
}));

describe("ProjectSettingsPage", () => {
  beforeEach(() => {
    permissions.canEdit = true;
  });

  it("keeps the form at 720 px and puts score configs under a self-saving heading after the save bar", () => {
    const { container } = render(<ProjectSettingsPage />);

    expect(container.firstElementChild).toHaveClass("max-w-[720px]");
    const heading = screen.getByRole("heading", { level: 2, name: "Saved as you change them" });
    const save = screen.getByRole("button", { name: "Save changes" });
    expect(save.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const anchor = container.querySelector("#score-configs");
    expect(anchor).toContainElement(heading);
    expect(anchor).toContainElement(screen.getByText("Score configs card"));
  });

  it("drops the heading but keeps the anchor when there is no save bar", () => {
    permissions.canEdit = false;
    const { container } = render(<ProjectSettingsPage />);

    expect(screen.queryByRole("heading", { name: "Saved as you change them" })).toBeNull();
    expect(container.querySelector("#score-configs")).toContainElement(
      screen.getByText("Score configs card"),
    );
  });
});
