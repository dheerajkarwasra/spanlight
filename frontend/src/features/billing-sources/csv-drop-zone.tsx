import { FileText, Upload, X } from "lucide-react";
import { useState, type DragEvent, type RefObject } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { formatFileSize } from "./csv-upload";

interface CsvDropZoneProps {
  /** The hidden file input; the dialog's "Choose another file" opens it too. */
  inputRef: RefObject<HTMLInputElement | null>;
  onChoose: (file: File) => void;
  /** The chosen file is refused: the zone's border turns rose. */
  invalid: boolean;
}

/** Figma "Reconciliation / csv upload dialog": drop a CSV here or browse for one. */
export function CsvDropZone({ inputRef, onChoose, invalid }: CsvDropZoneProps) {
  const [dragging, setDragging] = useState(false);

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) {
      onChoose(file);
    }
  }

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => {
        setDragging(false);
      }}
      onDrop={handleDrop}
      className={cn(
        "flex min-h-28 flex-col items-center justify-center gap-2 rounded-tile border-[1.5px] border-dashed bg-surface-muted px-4 py-6 text-center",
        invalid ? "border-danger" : dragging ? "border-accent" : "border-border-strong",
      )}
    >
      <Upload aria-hidden className="size-[18px] text-foreground" />
      <p className="text-sm font-semibold text-foreground">
        Drop a CSV file here or{" "}
        <Button
          variant="link"
          data-browse
          className="text-sm"
          onClick={() => {
            inputRef.current?.click();
          }}
        >
          browse
        </Button>
      </p>
      <p className="text-xs font-medium text-muted-foreground">
        UTF-8 · up to 5 MB and 100,000 rows
      </p>
    </div>
  );
}

interface CsvFileChipProps {
  file: File;
  /** "4 errors" once the server refused the file. */
  errorCount: number | null;
  onClear: () => void;
}

/** The chosen file under the zone: its name, size and, after a refusal, the error count. */
export function CsvFileChip({ file, errorCount, onClear }: CsvFileChipProps) {
  const details = [formatFileSize(file.size)];
  if (errorCount !== null) {
    details.push(`${String(errorCount)} ${errorCount === 1 ? "error" : "errors"}`);
  }

  return (
    <div className="flex items-center gap-3 rounded-tile border border-border bg-surface px-3.5 py-3">
      <FileText aria-hidden className="size-4 shrink-0 text-foreground" />
      <div className="flex min-w-0 flex-1 flex-col">
        <span title={file.name} className="truncate font-mono text-label text-foreground">
          {file.name}
        </span>
        <span
          className={cn(
            "text-xs font-medium",
            errorCount === null ? "text-muted-foreground" : "text-danger-text",
          )}
        >
          {details.join(" · ")}
        </span>
      </div>
      <Button variant="ghost" size="icon-sm" aria-label={`Remove ${file.name}`} onClick={onClear}>
        <X aria-hidden />
      </Button>
    </div>
  );
}
