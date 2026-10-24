import { Link } from "@tanstack/react-router";
import { FlaskConical, Lock, Plus, Trash2, Upload } from "lucide-react";
import type { ReactNode } from "react";

import { Breadcrumbs, Crumb } from "@/components/breadcrumbs";
import { DisabledReason } from "@/components/disabled-reason";
import { UnknownValue } from "@/components/unknown-value";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { usePermission, useProjectParams } from "@/features/shell";
import type { Dataset } from "@/lib/api";
import { formatDate, pluralize } from "@/lib/format";

import { MAX_ITEMS } from "./dataset-errors";
import { DeleteDatasetDialog } from "./delete-dataset-dialog";
import { EditDescriptionDialog } from "./edit-description-dialog";
import { DatasetLockAction } from "./lock-dataset-dialog";
import { UploadDialog } from "./upload-dialog";

/** "Datasets › <current>": back to the list. */
export function DatasetsBreadcrumbs({ current }: { current: string }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Breadcrumbs current={current}>
      <Crumb to="/$orgId/$projectId/datasets" params={{ orgId, projectId }}>
        Datasets
      </Crumb>
    </Breadcrumbs>
  );
}

/** Figma "Datasets/Locked badge": the lock icon plus the word, never colour alone. */
export function LockedBadge() {
  return (
    <Badge variant="outline">
      <Lock aria-hidden />
      Locked
    </Badge>
  );
}

interface DatasetHeaderProps {
  dataset: Dataset;
  /** `datasets:write` on an unlocked dataset: the item and delete actions. */
  canWrite: boolean;
  /** `datasets:lock`: the Lock or Unlock action. */
  canLock: boolean;
  onAddItem: () => void;
}

/** Figma detail header: name, item count (warning once full), lock state, description, actions. */
export function DatasetHeader({ dataset, canWrite, canLock, onAddItem }: DatasetHeaderProps) {
  const full = dataset.item_count >= MAX_ITEMS;
  // The lock guards items only; the server takes description edits on a locked dataset too.
  const canEditDescription = usePermission("datasets:write");
  const canRun = usePermission("experiments:run");
  const created = formatDate(dataset.created_at);
  const line = [dataset.description, created ? `created ${created}` : null]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between lg:gap-8">
      <div className="flex min-w-0 flex-1 flex-col gap-2 lg:min-w-64">
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          <h1 className="min-w-0 text-h1 [overflow-wrap:anywhere] text-foreground">
            {dataset.name}
          </h1>
          <Badge variant={full ? "warning" : "outline"}>
            {pluralize(dataset.item_count, "item")}
            {full ? " · full" : null}
          </Badge>
          {dataset.locked_at !== null ? (
            <span className="hidden sm:inline-flex">
              <LockedBadge />
            </span>
          ) : null}
        </div>
        {/* Frame 2 puts the badge by the name and "Locked since" last; at 375 px (630:79720) both
            share one row above the description, so the badge moves and the row is reordered. */}
        {line ? (
          <p className="max-w-2xl text-sm text-muted-foreground max-sm:order-1">{line}</p>
        ) : null}
        {dataset.locked_at !== null ? (
          <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <span className="sm:hidden">
              <LockedBadge />
            </span>
            <span>
              Locked since{" "}
              {formatDate(dataset.locked_at) ?? (
                <UnknownValue reason="The lock date could not be read." />
              )}
            </span>
          </p>
        ) : null}
      </div>
      {canWrite || canLock || canEditDescription || canRun ? (
        <div className="flex flex-wrap items-center gap-2 lg:justify-end">
          {canWrite ? (
            <DeleteDatasetDialog
              dataset={dataset}
              trigger={
                <Button variant="ghost">
                  <Trash2 aria-hidden />
                  Delete dataset
                </Button>
              }
            />
          ) : null}
          {canLock ? <DatasetLockAction dataset={dataset} /> : null}
          {canEditDescription ? <EditDescriptionDialog dataset={dataset} /> : null}
          {canWrite ? (
            <BlockedWhenFull full={full}>
              <UploadDialog
                datasetId={dataset.id}
                trigger={
                  <Button disabled={full}>
                    <Upload aria-hidden />
                    Upload file
                  </Button>
                }
              />
            </BlockedWhenFull>
          ) : null}
          {canRun ? <RunExperimentButton dataset={dataset} /> : null}
          {canWrite ? (
            <BlockedWhenFull full={full}>
              <Button variant="primary" disabled={full} onClick={onAddItem}>
                <Plus aria-hidden />
                Add item
              </Button>
            </BlockedWhenFull>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** A new experiment with this dataset already picked; an empty dataset has nothing to run. */
function RunExperimentButton({ dataset }: { dataset: Dataset }) {
  const { orgId, projectId } = useProjectParams();
  if (dataset.item_count === 0) {
    return (
      <DisabledReason reason="Add items first">
        <Button disabled>
          <FlaskConical aria-hidden />
          Run experiment
        </Button>
      </DisabledReason>
    );
  }
  return (
    <Button asChild>
      <Link
        to="/$orgId/$projectId/experiments/new"
        params={{ orgId, projectId }}
        search={{ dataset: dataset.id }}
      >
        <FlaskConical aria-hidden />
        Run experiment
      </Link>
    </Button>
  );
}

function BlockedWhenFull({ full, children }: { full: boolean; children: ReactNode }) {
  if (!full) return children;
  return (
    <DisabledReason reason="This dataset is full. Remove items to add new ones.">
      {children}
    </DisabledReason>
  );
}
