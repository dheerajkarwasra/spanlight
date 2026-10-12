import { Link } from "@tanstack/react-router";
import { CircleAlert, Info, Link2, Trash2 } from "lucide-react";
import { useId } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { useProjectParams } from "@/features/shell";
import type { DatasetItem } from "@/lib/api";
import { cn } from "@/lib/utils";

import { datasetErrorMessage } from "./dataset-errors";
import { useDeleteItem } from "./dataset-queries";
import { MAX_PAYLOAD_BYTES, jsonBytes, parseEditorText, sizeLabel } from "./item-values";

interface PayloadFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** Inputs of stored items cannot change; they show as text. */
  readOnly?: boolean;
  hint?: string;
  /** Why the text can't be saved (invalid JSON, too large); shown under the field. */
  problem?: string | null;
}

/** A payload textarea with its "0.1 KB of 64 KB" counter (turns rose past the limit). */
export function PayloadField({
  label,
  value,
  onChange,
  readOnly = false,
  hint,
  problem = null,
}: PayloadFieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const problemId = `${id}-problem`;
  const bytes = jsonBytes(parseEditorText(value));
  const tooLarge = bytes > MAX_PAYLOAD_BYTES;
  const describedBy = [problem ? problemId : null, hint ? hintId : null].filter(Boolean).join(" ");
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={id} className="text-label font-semibold text-foreground">
          {label}
        </label>
        <span
          className={cn(
            "text-xs",
            tooLarge ? "font-medium text-danger-text" : "text-muted-foreground",
          )}
        >
          {sizeLabel(bytes, MAX_PAYLOAD_BYTES)}
        </span>
      </div>
      <Textarea
        id={id}
        value={value}
        readOnly={readOnly}
        rows={readOnly ? 3 : 5}
        aria-invalid={problem ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={cn("max-h-80 resize-y", readOnly && "bg-surface-muted")}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
      {problem ? (
        <p id={problemId} className="flex items-start gap-1.5 text-xs font-medium text-danger-text">
          <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" />
          {problem}
        </p>
      ) : null}
      {hint ? (
        <p
          id={hintId}
          className="flex items-start gap-1.5 text-xs font-medium text-muted-foreground"
        >
          <Info aria-hidden className="mt-px size-3.5 shrink-0" />
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/** The trace an item was saved from, with a link to open it. */
export function SourceTraceTile({ traceId, spanId }: { traceId: string; spanId: string | null }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <div className="flex items-center gap-3 rounded-tile bg-surface-muted px-3.5 py-3">
      <Link2 aria-hidden className="size-4 shrink-0 text-accent" />
      <code title={traceId} className="min-w-0 flex-1 truncate text-label text-accent">
        {traceId}
      </code>
      <Link
        to="/$orgId/$projectId/traces/$traceId"
        params={{ orgId, projectId, traceId }}
        search={spanId ? { span: spanId } : {}}
        className="shrink-0 rounded-sm text-sm font-medium text-accent hover:underline"
      >
        Open trace
      </Link>
    </div>
  );
}

interface DeleteItemButtonProps {
  datasetId: string;
  item: DatasetItem;
  onDeleted: () => void;
}

/** "Delete item" in the editor's footer, behind a confirmation. */
export function DeleteItemButton({ datasetId, item, onDeleted }: DeleteItemButtonProps) {
  const remove = useDeleteItem(datasetId);
  return (
    <ConfirmDialog
      trigger={
        <Button variant="ghost" className="px-2 text-danger-text hover:bg-danger-subtle">
          <Trash2 aria-hidden />
          Delete item
        </Button>
      }
      title="Delete this item?"
      description="It leaves the dataset for good. This cannot be undone."
      confirmLabel="Delete item"
      onConfirm={() =>
        remove.mutateAsync(item.id).then(
          () => {
            toast.success("Deleted the item.");
            onDeleted();
          },
          (error: unknown) => {
            toast.error(datasetErrorMessage(error));
            throw error;
          },
        )
      }
    />
  );
}
