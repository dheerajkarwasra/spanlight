import { Plus, Trash2, Upload } from "lucide-react";
import type { ReactNode } from "react";

import { BackLink } from "@/components/back-link";
import { DisabledReason } from "@/components/disabled-reason";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useProjectParams } from "@/features/shell";
import type { Dataset } from "@/lib/api";
import { formatDate, pluralize } from "@/lib/format";

import { MAX_ITEMS } from "./dataset-errors";
import { DeleteDatasetDialog } from "./delete-dataset-dialog";
import { UploadDialog } from "./upload-dialog";

export function BackToDatasetsLink() {
  const { orgId, projectId } = useProjectParams();
  return (
    <BackLink to="/$orgId/$projectId/datasets" params={{ orgId, projectId }}>
      Datasets
    </BackLink>
  );
}

interface DatasetHeaderProps {
  dataset: Dataset;
  canWrite: boolean;
  onAddItem: () => void;
}

/** Figma detail header: name, item count (warning once full), description and the actions. */
export function DatasetHeader({ dataset, canWrite, onAddItem }: DatasetHeaderProps) {
  const full = dataset.item_count >= MAX_ITEMS;
  const created = formatDate(dataset.created_at);
  const line = [dataset.description, created ? `created ${created}` : null]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between lg:gap-8">
      <div className="flex min-w-0 flex-col gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          <h1 className="min-w-0 text-h1 [overflow-wrap:anywhere] text-foreground">
            {dataset.name}
          </h1>
          <Badge variant={full ? "warning" : "outline"}>
            {pluralize(dataset.item_count, "item")}
            {full ? " · full" : null}
          </Badge>
        </div>
        {line ? <p className="max-w-2xl text-sm text-muted-foreground">{line}</p> : null}
      </div>
      {canWrite ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <DeleteDatasetDialog
            dataset={dataset}
            trigger={
              <Button variant="ghost">
                <Trash2 aria-hidden />
                Delete dataset
              </Button>
            }
          />
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
          <BlockedWhenFull full={full}>
            <Button variant="primary" disabled={full} onClick={onAddItem}>
              <Plus aria-hidden />
              Add item
            </Button>
          </BlockedWhenFull>
        </div>
      ) : null}
    </div>
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
