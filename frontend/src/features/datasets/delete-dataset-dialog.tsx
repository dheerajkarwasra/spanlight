import { useState, type ReactElement } from "react";

import { DialogHeading } from "@/components/dialog-heading";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import type { Dataset } from "@/lib/api";
import { pluralize } from "@/lib/format";

import { DATASET_IN_USE_MESSAGE, datasetErrorMessage, isProblem } from "./dataset-errors";
import { useDeleteDataset } from "./dataset-queries";

/**
 * Figma "Datasets — Delete dialog" (confirm, and the in-use refusal). The server is the one that
 * knows whether an experiment uses the dataset, so the refusal shows after the attempt.
 */
export function DeleteDatasetDialog({
  dataset,
  trigger,
}: {
  dataset: Dataset;
  trigger: ReactElement;
}) {
  const [open, setOpen] = useState(false);
  const remove = useDeleteDataset();
  const inUse = isProblem(remove.error, "DATASET_IN_USE");

  function handleDelete() {
    remove.mutate(dataset);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next || !remove.isPending) {
          setOpen(next);
          if (!next) remove.reset();
        }
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent hideClose className="max-w-[440px] rounded-card p-6 sm:p-7">
        <DialogHeading
          title={`Delete “${dataset.name}”?`}
          description={`This removes the dataset and its ${pluralize(dataset.item_count, "item")}. It cannot be undone.`}
          showClose={remove.isPending ? "disabled" : true}
        />
        {inUse ? (
          <Notice tone="danger" role="alert">
            <p className="font-semibold">This dataset is in use</p>
            <p>{DATASET_IN_USE_MESSAGE}</p>
          </Notice>
        ) : remove.error ? (
          <Notice tone="danger" role="alert">
            {datasetErrorMessage(remove.error)}
          </Notice>
        ) : (
          <Notice>Traces it was built from are not affected.</Notice>
        )}
        <div className="flex justify-end gap-2.5">
          <DialogClose asChild>
            <Button disabled={remove.isPending}>{inUse ? "Close" : "Cancel"}</Button>
          </DialogClose>
          <Button
            variant="danger"
            loading={remove.isPending}
            disabled={inUse}
            onClick={handleDelete}
          >
            Delete dataset
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
