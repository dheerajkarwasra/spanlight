import { useNavigate } from "@tanstack/react-router";
import { useState, type ReactElement } from "react";

import { DialogHeading } from "@/components/dialog-heading";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { useProjectParams } from "@/features/shell";
import type { UploadResult } from "@/lib/api";
import { pluralize } from "@/lib/format";

import { datasetErrorMessage } from "./dataset-errors";
import { useDatasetOption, useUploadItems } from "./dataset-queries";
import { DatasetSelect } from "./dataset-select";
import { FileDrop } from "./file-drop";
import { UploadPreviewTable } from "./upload-preview-table";
import { UploadResultView } from "./upload-result";
import { useUploadFile } from "./use-upload-file";

interface UploadDialogProps {
  trigger: ReactElement;
  /** The dataset to preselect: the one whose page the dialog opens from. */
  datasetId?: string;
}

/** Figma "Datasets — Upload dialog": pick a dataset and a file, preview it, upload, see the result. */
export function UploadDialog({ trigger, datasetId = "" }: UploadDialogProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next || !pending) setOpen(next);
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {open ? (
        <DialogContent
          hideClose
          className="max-h-[calc(100dvh-2rem)] max-w-[560px] overflow-y-auto rounded-card p-6 sm:p-7"
        >
          <UploadFlow
            initialDatasetId={datasetId}
            onPendingChange={setPending}
            onDone={() => {
              setOpen(false);
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

interface UploadFlowProps {
  initialDatasetId: string;
  onPendingChange: (pending: boolean) => void;
  onDone: () => void;
}

function UploadFlow({ initialDatasetId, onPendingChange, onDone }: UploadFlowProps) {
  const navigate = useNavigate();
  const { orgId, projectId } = useProjectParams();
  const [datasetId, setDatasetId] = useState(initialDatasetId);
  const [result, setResult] = useState<UploadResult | null>(null);
  const { chosen, choose, clear } = useUploadFile();
  const upload = useUploadItems();
  const dataset = useDatasetOption(datasetId);
  const datasetName = dataset ? `“${dataset.name}”` : "the dataset";

  function handleUpload() {
    if (!chosen) return;
    onPendingChange(true);
    upload.mutate(
      { datasetId, file: chosen.file },
      {
        onSuccess: setResult,
        onSettled: () => {
          onPendingChange(false);
        },
      },
    );
  }

  if (result && chosen) {
    return (
      <div className="flex flex-col gap-5">
        <DialogHeading
          title="Upload finished"
          description={`${chosen.file.name} was added to ${datasetName}. Rejected lines were skipped; the rest was accepted.`}
        />
        <UploadResultView result={result} />
        <div className="flex flex-wrap justify-end gap-2.5">
          <Button
            onClick={() => {
              setResult(null);
              upload.reset();
              clear();
            }}
          >
            Upload another
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              onDone();
              void navigate({
                to: "/$orgId/$projectId/datasets/$datasetId",
                params: { orgId, projectId, datasetId },
              });
            }}
          >
            View dataset
          </Button>
        </div>
      </div>
    );
  }

  const preview = chosen?.problem ? null : chosen?.preview;
  const ready = Boolean(datasetId) && preview != null && preview.valid > 0;
  return (
    <div className="flex flex-col gap-5">
      <DialogHeading
        title="Upload items"
        description={`Add items to ${datasetName} from a .jsonl or .csv file. Each item needs an input.`}
        showClose={upload.isPending ? "disabled" : true}
      />
      <DatasetSelect
        value={datasetId}
        onChange={setDatasetId}
        hint="Not the right dataset? Pick another, or create one first."
      />
      <FileDrop chosen={chosen} onChoose={choose} onClear={clear} />
      {chosen?.problem ? (
        <Notice tone="danger" role="alert">
          <p className="font-semibold">{chosen.problem.title}</p>
          <p>{chosen.problem.detail}</p>
        </Notice>
      ) : null}
      {upload.error ? (
        <Notice tone="danger" role="alert">
          {datasetErrorMessage(upload.error)}
        </Notice>
      ) : null}
      {preview ? (
        <UploadPreviewTable preview={preview} />
      ) : (
        <Notice>
          JSONL: one object per line, with input required. CSV: a header row with an input column;
          other columns become metadata.
        </Notice>
      )}
      <div className="flex justify-end gap-2.5">
        <DialogClose asChild>
          <Button disabled={upload.isPending}>Cancel</Button>
        </DialogClose>
        <Button
          variant="primary"
          disabled={!ready}
          loading={upload.isPending}
          onClick={handleUpload}
        >
          {preview ? `Upload ${pluralize(preview.valid, "item")}` : "Upload file"}
        </Button>
      </div>
    </div>
  );
}
