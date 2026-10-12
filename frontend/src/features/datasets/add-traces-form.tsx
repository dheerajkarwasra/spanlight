import { Check, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { DialogHeading } from "@/components/dialog-heading";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import type { TracesAdded } from "@/lib/api";
import { pluralize, shortId } from "@/lib/format";

import { datasetErrorMessage, rejectionLabel } from "./dataset-errors";
import { useAddItemsFromTraces, useDatasetOption } from "./dataset-queries";
import { DatasetSelect } from "./dataset-select";
import type { TraceRef } from "./span-pick";

/** The API takes at most this many trace ids per request. */
export const MAX_TRACES_PER_ADD = 200;

interface AddTracesFormProps {
  traces: TraceRef[];
  onPendingChange: (pending: boolean) => void;
  /** Items were added; the dialog acts on it when it closes. */
  onAdded: () => void;
  onDone: () => void;
}

/**
 * Figma "Add to dataset dialog — bulk": every selected trace adds one item from its first LLM
 * span. Which traces the server refused, and why, shows once it answers.
 */
export function AddTracesForm({ traces, onPendingChange, onAdded, onDone }: AddTracesFormProps) {
  const [datasetId, setDatasetId] = useState("");
  const [result, setResult] = useState<TracesAdded | null>(null);
  const add = useAddItemsFromTraces();
  const dataset = useDatasetOption(datasetId);
  const tooMany = traces.length > MAX_TRACES_PER_ADD;
  const reasons = new Map(result?.rejected.map((entry) => [entry.trace_id, entry.reason]));

  function submit() {
    onPendingChange(true);
    add.mutate(
      { datasetId, traceIds: traces.map((trace) => trace.trace_id) },
      {
        onSuccess: (added) => {
          onAdded();
          if (added.rejected.length === 0) {
            toast.success(
              `Added ${pluralize(added.accepted, "item")} to “${dataset?.name ?? "the dataset"}”.`,
            );
            onDone();
            return;
          }
          setResult(added);
        },
        onSettled: () => {
          onPendingChange(false);
        },
      },
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <DialogHeading
        title={`Add ${pluralize(traces.length, "trace")} to a dataset`}
        description="Each trace adds one item from its first LLM span, its input and recorded output."
        showClose={add.isPending ? "disabled" : true}
      />
      {result ? (
        <Notice tone="warning" role="status">
          Added {pluralize(result.accepted, "item")}. {pluralize(result.rejected.length, "trace")}{" "}
          could not be added; the reasons are below.
        </Notice>
      ) : (
        <DatasetSelect value={datasetId} onChange={setDatasetId} />
      )}
      <div className="flex flex-col gap-2">
        <span className="text-label font-semibold text-foreground">Traces</span>
        <ul className="flex max-h-64 flex-col gap-1 overflow-y-auto">
          {traces.map((trace) => (
            <TraceLine
              key={trace.trace_id}
              trace={trace}
              done={result !== null}
              reason={reasons.get(trace.trace_id) ?? null}
            />
          ))}
        </ul>
      </div>
      {tooMany ? (
        <Notice tone="warning">Pick at most {MAX_TRACES_PER_ADD} traces at a time.</Notice>
      ) : null}
      {add.error ? (
        <Notice tone="danger" role="alert">
          {datasetErrorMessage(add.error)}
        </Notice>
      ) : null}
      <div className="flex justify-end gap-2.5">
        {result ? (
          <Button variant="primary" onClick={onDone}>
            Done
          </Button>
        ) : (
          <>
            <DialogClose asChild>
              <Button disabled={add.isPending}>Cancel</Button>
            </DialogClose>
            <Button
              variant="primary"
              disabled={!datasetId || tooMany}
              loading={add.isPending}
              onClick={submit}
            >
              Add {pluralize(traces.length, "item")}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function TraceLine({
  trace,
  done,
  reason,
}: {
  trace: TraceRef;
  done: boolean;
  reason: string | null;
}) {
  return (
    <li className="flex items-center gap-2 rounded-input bg-surface-muted px-3 py-2 text-xs">
      <span className="truncate font-semibold text-foreground">
        {trace.name ?? "Unnamed trace"}
      </span>
      <code className="shrink-0 text-accent">{shortId(trace.trace_id)}</code>
      {done ? (
        reason === null ? (
          <span className="ml-auto flex shrink-0 items-center gap-1 text-success">
            <Check aria-hidden className="size-3.5" />
            Added
          </span>
        ) : (
          <span className="ml-auto flex shrink-0 items-center gap-1 text-danger-text">
            <TriangleAlert aria-hidden className="size-3.5" />
            {rejectionLabel(reason)}, rejected
          </span>
        )
      ) : null}
    </li>
  );
}
