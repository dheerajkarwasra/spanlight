import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError, type Project } from "@/lib/api";

import { ProjectSettingsForm } from "./project-settings-form";

const mutate = vi.fn();

vi.mock("./use-update-project", () => ({
  useUpdateProject: () => ({ mutate, isPending: false }),
}));
vi.mock("./insight-channels-field", () => ({ InsightChannelsField: () => null }));
vi.mock("@/features/shell", () => ({
  useFilterOptionsQuery: () => ({ data: { metadata_keys: [] } }),
}));

const project: Project = {
  id: "proj_1",
  org_id: "org_1",
  name: "Support bot",
  slug: "support-bot",
  retention_days: 30,
  capture_payloads: true,
  weekly_digest_enabled: true,
  insight_channel_ids: [],
  promoted_metadata_keys: ["customer_id"],
  created_at: "2026-10-01T00:00:00Z",
};

describe("ProjectSettingsForm", () => {
  it("shows a refused promoted key under the field", async () => {
    mutate.mockImplementation((_update, { onError }: { onError: (error: unknown) => void }) => {
      onError(
        new ApiError(422, {
          code: "VALIDATION_FAILED",
          errors: [{ field: "promoted_metadata_keys.1", message: "at most 5 promoted keys" }],
        }),
      );
    });
    const user = userEvent.setup();
    render(
      <TooltipProvider>
        <ProjectSettingsForm project={project} canEdit />
      </TooltipProvider>,
    );

    await user.type(screen.getByRole("combobox", { name: "Promoted keys" }), "plan{Enter}");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(mutate).toHaveBeenCalledWith(
      { promoted_metadata_keys: ["customer_id", "plan"] },
      expect.anything(),
    );
    expect(screen.getByText("at most 5 promoted keys")).toBeInTheDocument();
  });
});
