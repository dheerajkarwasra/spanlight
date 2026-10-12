import { getRouteApi, Link } from "@tanstack/react-router";
import { SearchX, Upload } from "lucide-react";
import { useState } from "react";

import { Callout } from "@/components/callout";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams, usePermission } from "@/features/shell";
import { isApiError, type Dataset } from "@/lib/api";

import { BackToDatasetsLink, DatasetHeader } from "./dataset-header";
import { DATASET_FULL_MESSAGE, DATASET_FULL_TITLE, MAX_ITEMS } from "./dataset-errors";
import { useDatasetQuery } from "./dataset-queries";
import { ItemEditor, type ItemEditorTarget } from "./item-editor";
import { ItemTableSkeleton } from "./item-table";
import { ItemsCard } from "./items-card";
import { UploadDialog } from "./upload-dialog";

const datasetRoute = getRouteApi("/_authed/$orgId/$projectId/datasets/$datasetId");

const PAGE_CLASSES = "flex flex-col gap-5 md:gap-6";

/** Figma "Datasets — Detail": header, the full warning, the items and the item editor sheet. */
export function DatasetDetailPage() {
  const { datasetId } = datasetRoute.useParams();
  const query = useDatasetQuery(datasetId);

  if (query.isPending) {
    return (
      <div className={PAGE_CLASSES} aria-busy>
        <span className="sr-only" role="status">
          Loading dataset
        </span>
        <BackToDatasetsLink />
        <Skeleton className="h-9 w-72 max-w-full" />
        <Card className="p-6">
          <ItemTableSkeleton />
        </Card>
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className={PAGE_CLASSES}>
        <BackToDatasetsLink />
        <Card>
          {isApiError(query.error) && query.error.isNotFound ? (
            <DatasetNotFound />
          ) : (
            <ErrorState
              error={query.error}
              title="Couldn't load this dataset"
              onRetry={() => void query.refetch()}
            />
          )}
        </Card>
      </div>
    );
  }
  return <DatasetDetail dataset={query.data} />;
}

function DatasetDetail({ dataset }: { dataset: Dataset }) {
  const canWrite = usePermission("datasets:write");
  const [target, setTarget] = useState<ItemEditorTarget | null>(null);
  const full = dataset.item_count >= MAX_ITEMS;
  const addItem = () => {
    setTarget({ item: null });
  };

  return (
    <div className={PAGE_CLASSES}>
      <BackToDatasetsLink />
      <DatasetHeader dataset={dataset} canWrite={canWrite} onAddItem={addItem} />
      {full ? (
        <Callout tone="warning" title={DATASET_FULL_TITLE}>
          {DATASET_FULL_MESSAGE}
        </Callout>
      ) : null}
      <ItemsCard
        dataset={dataset}
        canWrite={canWrite}
        onAddItem={addItem}
        onOpen={(item, number) => {
          setTarget({ item, number });
        }}
        uploadAction={
          <UploadDialog
            datasetId={dataset.id}
            trigger={
              <Button>
                <Upload aria-hidden />
                Upload file
              </Button>
            }
          />
        }
      />
      <ItemEditor
        datasetId={dataset.id}
        target={target}
        canWrite={canWrite}
        onClose={() => {
          setTarget(null);
        }}
      />
    </div>
  );
}

function DatasetNotFound() {
  const { orgId, projectId } = useProjectParams();
  return (
    <EmptyState
      icon={SearchX}
      title="Dataset not found"
      description="It may have been deleted, or the link is wrong."
      action={
        <Button asChild size="sm">
          <Link to="/$orgId/$projectId/datasets" params={{ orgId, projectId }}>
            Back to datasets
          </Link>
        </Button>
      }
    />
  );
}
