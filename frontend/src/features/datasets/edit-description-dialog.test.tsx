import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type * as Shell from "@/features/shell";
import { datasetsApi, type Dataset } from "@/lib/api";

import { DatasetHeader } from "./dataset-header";
import { EditDescriptionDialog } from "./edit-description-dialog";

vi.mock("@/features/shell", async (importOriginal) => ({
  ...(await importOriginal<typeof Shell>()),
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
  // Only datasets:write: "Run experiment" is a router Link, and this test renders no router.
  usePermission: (permission: string) => permission === "datasets:write" && canWriteDatasets,
}));

let canWriteDatasets = true;

const DATASET: Dataset = {
  id: "ds_1",
  name: "support-golden",
  description: "Golden answers for support triage",
  item_count: 48,
  locked_at: null,
  created_at: "2026-10-02T00:00:00Z",
  updated_at: "2026-10-02T00:00:00Z",
};

function withClient(children: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("EditDescriptionDialog", () => {
  it("saves the edited description and only the description", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(datasetsApi, "update").mockResolvedValue(DATASET);
    withClient(<EditDescriptionDialog dataset={DATASET} />);

    await user.click(screen.getByRole("button", { name: "Edit description" }));
    const field = screen.getByLabelText("Description");
    expect(field).toHaveValue("Golden answers for support triage");
    expect(screen.getByText("33 / 1,000")).toBeInTheDocument();

    await user.clear(field);
    await user.type(field, "  Triage answers  ");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(update).toHaveBeenCalledWith("proj_1", "ds_1", { description: "Triage answers" });
  });

  it("clears the description when the field is left empty", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(datasetsApi, "update").mockResolvedValue(DATASET);
    withClient(<EditDescriptionDialog dataset={DATASET} />);

    await user.click(screen.getByRole("button", { name: "Edit description" }));
    await user.clear(screen.getByLabelText("Description"));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(update).toHaveBeenCalledWith("proj_1", "ds_1", { description: null });
  });

  it("is offered to a writer on a locked dataset: the lock only guards items", () => {
    canWriteDatasets = true;
    withClient(
      <DatasetHeader
        dataset={{ ...DATASET, locked_at: "2026-10-10T00:00:00Z" }}
        canWrite={false}
        canLock={false}
        onAddItem={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "Edit description" })).toBeInTheDocument();
  });

  it("is not offered to a role that can't write datasets", () => {
    canWriteDatasets = false;
    withClient(
      <DatasetHeader dataset={DATASET} canWrite={false} canLock={false} onAddItem={() => {}} />,
    );
    expect(screen.queryByRole("button", { name: "Edit description" })).not.toBeInTheDocument();
  });
});
