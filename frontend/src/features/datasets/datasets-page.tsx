import { Plus, Upload } from "lucide-react";

import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { usePermission } from "@/features/shell";

import { CreateDatasetDialog } from "./create-dataset-dialog";
import { DatasetList } from "./dataset-list";
import { UploadDialog } from "./upload-dialog";

/** Figma "Datasets — List": the project's test sets, a new dataset and an upload. */
export function DatasetsPage() {
  const canWrite = usePermission("datasets:write");
  const createButton = (
    <CreateDatasetDialog
      trigger={
        <Button variant="primary">
          <Plus aria-hidden />
          New dataset
        </Button>
      }
    />
  );

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <PageHeader
        title="Datasets"
        description="Test sets of inputs and expected outputs. Run a prompt against one and compare the results item by item."
        actions={
          canWrite ? (
            <>
              <UploadDialog
                trigger={
                  <Button>
                    <Upload aria-hidden />
                    Upload file
                  </Button>
                }
              />
              {createButton}
            </>
          ) : null
        }
      />
      <DatasetList createAction={canWrite ? createButton : null} />
    </div>
  );
}
