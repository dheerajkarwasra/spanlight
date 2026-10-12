import { Info } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { DialogHeading } from "@/components/dialog-heading";
import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import type { AnnotationQueue, QueueItemsAdded, Span } from "@/lib/api";
import { pluralize } from "@/lib/format";

import { datasetErrorMessage } from "./dataset-errors";
import { useAddToQueue, useQueueOptionsQuery, useScoreConfigsQuery } from "./dataset-queries";
import { MAX_TRACES_PER_ADD } from "./add-traces-form";
import { SpanChoice } from "./span-choice";
import { spanLabel, type TraceRef } from "./span-pick";

interface AddToQueueFormProps {
  traces: TraceRef[];
  selectedSpan: Span | null;
  onPendingChange: (pending: boolean) => void;
  onDone: () => void;
}

export function AddToQueueForm({
  traces,
  selectedSpan,
  onPendingChange,
  onDone,
}: AddToQueueFormProps) {
  const queues = useQueueOptionsQuery();
  const [queueId, setQueueId] = useState("");
  const [scope, setScope] = useState("trace");
  const add = useAddToQueue();
  const queue = queues.data?.items.find((candidate) => candidate.id === queueId);
  const single = traces.length === 1;
  const tooMany = traces.length > MAX_TRACES_PER_ADD;

  function submit() {
    onPendingChange(true);
    add.mutate(
      {
        queueId,
        traceIds: traces.map((trace) => trace.trace_id),
        spanId: scope === "span" ? selectedSpan?.span_id : null,
      },
      {
        onSuccess: (result) => {
          toast.success(addedMessage(result, queue?.name ?? "the queue"));
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
        title={
          single ? "Add to annotation queue" : `Add ${pluralize(traces.length, "trace")} to a queue`
        }
        description={
          single
            ? "Reviewers will see this trace in the queue and score it by hand."
            : "Reviewers will see these traces in the queue and score them by hand."
        }
        showClose={add.isPending ? "disabled" : true}
      />
      {queues.isPending ? (
        <Skeleton className="h-[46px] w-full rounded-input" />
      ) : queues.isError ? (
        <Notice tone="danger" role="alert">
          Couldn't load the review queues.{" "}
          <Button variant="link" size="sm" onClick={() => void queues.refetch()}>
            Try again
          </Button>
        </Notice>
      ) : queues.data.items.length === 0 ? (
        <Notice>This project has no review queues yet. Create one on the Review page first.</Notice>
      ) : (
        <FormField label="Queue" hint={queue ? <QueueScoresHint queue={queue} /> : undefined}>
          <FieldSelect
            value={queueId}
            onChange={setQueueId}
            placeholder="Pick a queue"
            options={queues.data.items.map((item) => ({ value: item.id, label: item.name }))}
          />
        </FormField>
      )}
      {single && selectedSpan ? (
        <SpanChoice
          label="What to review"
          value={scope}
          onChange={setScope}
          options={[
            { value: "trace", title: "Whole trace", detail: "Reviewers see every span" },
            { value: "span", title: "Selected span", detail: spanLabel(selectedSpan) },
          ]}
        />
      ) : null}
      <p className="flex items-start gap-1.5 text-xs font-medium text-muted-foreground">
        <Info aria-hidden className="mt-px size-3.5 shrink-0" />A trace or span that is already in
        this queue is skipped.
      </p>
      {tooMany ? (
        <Notice tone="warning">Pick at most {MAX_TRACES_PER_ADD} traces at a time.</Notice>
      ) : null}
      {add.error ? (
        <Notice tone="danger" role="alert">
          {datasetErrorMessage(add.error)}
        </Notice>
      ) : null}
      <div className="flex justify-end gap-2.5">
        <DialogClose asChild>
          <Button disabled={add.isPending}>Cancel</Button>
        </DialogClose>
        <Button
          variant="primary"
          disabled={!queueId || tooMany}
          loading={add.isPending}
          onClick={submit}
        >
          Add to queue
        </Button>
      </div>
    </div>
  );
}

/** "3 scores per item: relevance, format, tone". */
function QueueScoresHint({ queue }: { queue: AnnotationQueue }) {
  const configs = useScoreConfigsQuery();
  const names = queue.score_config_ids
    .map((id) => configs.data?.find((config) => config.id === id)?.name)
    .filter((name): name is string => name !== undefined);
  const count = pluralize(queue.score_config_ids.length, "score");
  return names.length > 0 ? `${count} per item: ${names.join(", ")}` : `${count} per item`;
}

function addedMessage(result: QueueItemsAdded, queueName: string): string {
  if (result.added === 0) {
    return `Already in “${queueName}”. Nothing was added.`;
  }
  const skipped =
    result.skipped_duplicates > 0
      ? ` ${pluralize(result.skipped_duplicates, "was", "were")} already there.`
      : "";
  return `Added ${pluralize(result.added, "item")} to “${queueName}”.${skipped}`;
}
