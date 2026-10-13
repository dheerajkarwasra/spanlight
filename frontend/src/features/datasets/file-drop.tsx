import { FileText, Upload, X } from "lucide-react";
import { useId, useRef, useState, type DragEvent } from "react";

import { Button } from "@/components/ui/button";
import { pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import type { ChosenFile } from "./use-upload-file";

interface FileDropProps {
  chosen: ChosenFile | null;
  onChoose: (file: File) => void;
  onClear: () => void;
}

/**
 * Figma upload drop zone: drop a file or browse for one; once chosen, the file's name, size,
 * kind and line count with a remove mark. Dashed rose while the file has a problem.
 */
export function FileDrop({ chosen, onChoose, onClear }: FileDropProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const [dragging, setDragging] = useState(false);
  const invalid = chosen?.problem != null;

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) onChoose(file);
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
        "flex min-h-28 flex-col items-center justify-center gap-2 rounded-tile border-[1.5px] border-dashed bg-surface-muted px-4 py-5 text-center",
        invalid ? "border-danger" : dragging ? "border-accent" : "border-border-strong",
      )}
    >
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        accept=".jsonl,.csv"
        aria-label="Choose a .jsonl or .csv file"
        className="sr-only"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onChoose(file);
          event.target.value = "";
        }}
      />
      {chosen && !invalid ? (
        <ChosenFileChip chosen={chosen} onClear={onClear} />
      ) : (
        <>
          <span className="flex size-10 items-center justify-center rounded-full bg-surface shadow-card">
            <Upload aria-hidden className="size-4 text-accent" />
          </span>
          <p className="text-sm font-semibold text-foreground">Drop a .jsonl or .csv file here</p>
          <p className="text-xs font-medium text-muted-foreground">
            or{" "}
            <Button
              variant="link"
              size="sm"
              className="text-xs"
              onClick={() => inputRef.current?.click()}
            >
              browse your files
            </Button>
            . Up to 5 MB, 10,000 items per dataset.
          </p>
        </>
      )}
    </div>
  );
}

function ChosenFileChip({ chosen, onClear }: { chosen: ChosenFile; onClear: () => void }) {
  const kind = chosen.preview?.kind.toUpperCase();
  const details = [
    `${String(Math.max(1, Math.round(chosen.file.size / 1024)))} KB`,
    kind,
    chosen.preview ? pluralize(chosen.preview.total, "line") : "Reading…",
  ].filter(Boolean);
  return (
    <div className="flex max-w-full items-center gap-3">
      <FileText aria-hidden className="size-5 shrink-0 text-accent" />
      <div className="flex min-w-0 flex-col text-left">
        <span className="truncate text-sm font-semibold text-foreground">{chosen.file.name}</span>
        <span className="text-xs font-medium text-muted-foreground" aria-live="polite">
          {details.join(" · ")}
        </span>
      </div>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={`Remove ${chosen.file.name}`}
        className="bg-surface"
        onClick={onClear}
      >
        <X aria-hidden />
      </Button>
    </div>
  );
}
