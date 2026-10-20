import { Info } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { DialogHeading } from "@/components/dialog-heading";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import type { Span, TraceDetail, TracesAdded } from "@/lib/api";

import { AlsoInNote } from "./also-in-note";
import { datasetErrorMessage, rejectionLabel } from "./dataset-errors";
import { useAddItemsFromTraces, useDatasetOption } from "./dataset-queries";
import { DatasetSelect } from "./dataset-select";
import { valueText } from "./item-values";
import { SpanChoice, type SpanChoiceOption } from "./span-choice";
import { firstLlmSpan, hasPayload, spanLabel } from "./span-pick";

const RECORDED_NOTE = "Edit the expected output; the recorded output is only a starting point.";

interface AddTraceFormProps {
  trace: TraceDetail;
  selectedSpan: Span | null;
  onPendingChange: (pending: boolean) => void;
  onDone: () => void;
}

/** One trace: pick the dataset and the span, see what will be saved, add it. */
export function AddTraceForm({ trace, selectedSpan, onPendingChange, onDone }: AddTraceFormProps) {
  const firstLlm = firstLlmSpan(trace.spans);
  const [datasetId, setDatasetId] = useState("");
  const [choice, setChoice] = useState(firstLlm || !selectedSpan ? "first" : "selected");
  const [rejection, setRejection] = useState<string | null>(null);
  // Set when the item was added but other datasets already hold the trace: the dialog stays open.
  const [added, setAdded] = useState<TracesAdded | null>(null);
  const add = useAddItemsFromTraces();
  const dataset = useDatasetOption(datasetId);
  const span = choice === "selected" ? selectedSpan : firstLlm;
  const missingPayload = span !== null && !hasPayload(span);

  function submit() {
    setRejection(null);
    onPendingChange(true);
    add.mutate(
      {
        datasetId,
        traceIds: [trace.trace_id],
        spanId: choice === "selected" ? selectedSpan?.span_id : null,
      },
      {
        onSuccess: (result) => {
          const rejected = result.rejected[0];
          if (rejected) {
            setRejection(rejectionLabel(rejected.reason));
            return;
          }
          if (result.also_in.length > 0) {
            setAdded(result);
            return;
          }
          toast.success(`Added to “${dataset?.name ?? "the dataset"}”. ${RECORDED_NOTE}`);
          onDone();
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
        title="Add to dataset"
        description="Saves this trace's span input and output as a dataset item."
        showClose={add.isPending ? "disabled" : true}
      />
      {added ? (
        <AlsoInNote accepted={added.accepted} rejected={0} alsoIn={added.also_in} />
      ) : (
        <>
          <DatasetSelect value={datasetId} onChange={setDatasetId} />
          <SpanChoice
            label="Which span"
            value={choice}
            onChange={(next) => {
              setChoice(next);
              setRejection(null);
            }}
            options={spanOptions(firstLlm, selectedSpan)}
          />
        </>
      )}
      {missingPayload || rejection ? (
        <Notice tone="danger" role="alert">
          <p className="font-semibold">
            Not added: {missingPayload ? "no payload stored" : rejection?.toLowerCase()}
          </p>
          {missingPayload ? (
            <p>
              The selected span has no stored input or output, so there is nothing to save. Pick
              another span, or turn on content capture in the SDK.
            </p>
          ) : null}
        </Notice>
      ) : span ? (
        <SavedPreview span={span} saved={added !== null} />
      ) : null}
      {add.error ? (
        <Notice tone="danger" role="alert">
          {datasetErrorMessage(add.error)}
        </Notice>
      ) : null}
      <div className="flex justify-end gap-2.5">
        {added ? (
          <Button variant="primary" autoFocus onClick={onDone}>
            Done
          </Button>
        ) : (
          <>
            <DialogClose asChild>
              <Button disabled={add.isPending}>Cancel</Button>
            </DialogClose>
            <Button
              variant="primary"
              disabled={!datasetId || span === null || missingPayload}
              loading={add.isPending}
              onClick={submit}
            >
              Add to dataset
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function spanOptions(firstLlm: Span | null, selected: Span | null): SpanChoiceOption[] {
  const options: SpanChoiceOption[] = [
    {
      value: "first",
      title: "First LLM span",
      detail: firstLlm ? `${firstLlm.name} · the default` : "This trace has no LLM span",
      disabled: firstLlm === null,
    },
  ];
  if (selected) {
    options.push({
      value: "selected",
      title: "Selected span",
      detail: hasPayload(selected)
        ? spanLabel(selected)
        : `${spanLabel(selected)}, no stored payload`,
    });
  }
  return options;
}

function SavedPreview({ span, saved }: { span: Span; saved: boolean }) {
  const omitted = "Not previewed: this trace's payloads are too large to load at once.";
  return (
    <div className="flex flex-col gap-3">
      <PreviewBlock
        label={saved ? "Saved input" : "Input to be saved"}
        text={span.payload_omitted ? omitted : valueText(span.input)}
      />
      <PreviewBlock
        label={
          saved ? "Saved expected output" : "Expected output (recorded output, edit after adding)"
        }
        text={
          span.payload_omitted
            ? omitted
            : span.output == null
              ? "No recorded output. Add the expected answer after adding."
              : valueText(span.output)
        }
      />
      <p className="flex items-start gap-1.5 text-xs font-medium text-muted-foreground">
        <Info aria-hidden className="mt-px size-3.5 shrink-0" />
        {RECORDED_NOTE}
      </p>
    </div>
  );
}

function PreviewBlock({ label, text }: { label: string; text: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-overline text-muted-foreground uppercase">{label}</p>
      <p className="line-clamp-3 rounded-input bg-surface-muted px-3 py-2.5 text-sm [overflow-wrap:anywhere] text-foreground">
        {text}
      </p>
    </div>
  );
}
