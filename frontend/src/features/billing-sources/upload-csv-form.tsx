import { Download } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { Button } from "@/components/ui/button";
import { DialogClose, DialogFooter } from "@/components/ui/dialog";
import { errorMessage, type BillingSource } from "@/lib/api";
import { pluralize } from "@/lib/format";

import { useUploadCsv } from "./billing-sources-queries";
import { CsvDropZone, CsvFileChip } from "./csv-drop-zone";
import { CsvErrors } from "./csv-errors";
import {
  REQUIRED_COLUMNS_NOTE,
  checkCsvFile,
  csvErrorSummary,
  csvErrorsOf,
  downloadCsvTemplate,
  type CsvRowError,
} from "./csv-upload";

interface UploadCsvFormProps {
  source: BillingSource;
  onPendingChange: (pending: boolean) => void;
  onDone: () => void;
}

/**
 * The upload dialog's body: the file, the refused file's reasons, and Upload or "Choose another
 * file". The file lives in this state only and leaves with the dialog.
 */
export function UploadCsvForm({ source, onPendingChange, onDone }: UploadCsvFormProps) {
  const upload = useUploadCsv();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<CsvRowError[] | null>(null);

  useEffect(() => {
    onPendingChange(upload.pending);
    return () => {
      onPendingChange(false);
    };
  }, [upload.pending, onPendingChange]);

  function choose(next: File | null) {
    setFile(next);
    setRowErrors(null);
    setProblem(next ? checkCsvFile(next) : null);
  }

  async function send() {
    if (rowErrors) {
      inputRef.current?.click();
      return;
    }
    if (!file || problem) {
      setProblem(problem ?? "Choose the CSV file to upload.");
      return;
    }
    const result = await upload.run(source.id, file);
    if (result.ok) {
      const { lines_upserted: lines, days } = result.value;
      toast.success(
        `Imported ${pluralize(lines, "line")} covering ${pluralize(days.length, "day")} into ${source.name}.`,
      );
      onDone();
      return;
    }
    const errors = csvErrorsOf(result.error);
    if (errors) {
      setRowErrors(errors);
    } else {
      setProblem(errorMessage(result.error));
    }
  }

  return (
    <form
      noValidate
      className="grid gap-[22px]"
      onSubmit={(event) => {
        event.preventDefault();
        void send();
      }}
    >
      <DialogHeading
        title="Upload cost CSV"
        description={`Lines for ${source.name} from the provider’s cost export. Each upload replaces the provider figures for every day from its first to its last day.`}
        showClose={upload.pending ? "disabled" : true}
      />
      <input
        ref={inputRef}
        type="file"
        accept=".csv,text/csv"
        aria-label="Choose a CSV file"
        className="sr-only"
        tabIndex={-1}
        onChange={(event) => {
          const next = event.target.files?.[0];
          if (next) {
            choose(next);
          }
          event.target.value = "";
        }}
      />
      {rowErrors && file ? (
        <Callout tone="danger" title="Nothing was imported" role="alert">
          {csvErrorSummary(file.name, rowErrors)}
        </Callout>
      ) : (
        <CsvDropZone inputRef={inputRef} onChoose={choose} invalid={problem !== null} />
      )}
      {file ? (
        <CsvFileChip
          file={file}
          errorCount={rowErrors ? rowErrors.length : null}
          onClear={() => {
            choose(null);
          }}
        />
      ) : null}
      {problem ? (
        <Callout tone="danger" role="alert">
          {problem}
        </Callout>
      ) : null}
      {rowErrors ? (
        <CsvErrors errors={rowErrors} />
      ) : (
        <p className="text-xs font-medium text-muted-foreground">{REQUIRED_COLUMNS_NOTE}</p>
      )}
      <Button variant="link" className="justify-self-start" onClick={downloadCsvTemplate}>
        Download the CSV template
        <Download aria-hidden />
      </Button>
      <DialogFooter className="flex-row justify-end gap-2.5 max-sm:[&>*]:flex-1">
        <DialogClose asChild>
          <Button disabled={upload.pending}>Cancel</Button>
        </DialogClose>
        <Button type="submit" variant="primary" loading={upload.pending}>
          {rowErrors ? "Choose another file" : "Upload"}
        </Button>
      </DialogFooter>
    </form>
  );
}
