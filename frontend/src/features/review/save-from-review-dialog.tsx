import { Info } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { DialogHeading } from "@/components/dialog-heading";
import { Notice } from "@/components/notice";
import { UnknownValue } from "@/components/unknown-value";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import {
  DatasetSelect,
  datasetErrorMessage,
  rejectionLabel,
  useDatasetOption,
} from "@/features/datasets";
import type { QueueItem, TraceDetail } from "@/lib/api";

import { reasonLabel } from "./reason-copy";
import { useSaveFromReview } from "./review-queries";
import { RECORDED_NOTE, reviewMetadata, saveSpanOf, type SaveSpan } from "./save-from-review";

/** The queue side of the save: where the item sits and the reviewer's current label. */
export interface ReviewSource {
  queueId: string;
  /** "Daily review · item 25". */
  queueLabel: string;
  /** The draft's `failure_category` value; null when none is picked (or the queue has none). */
  failureCategory: string | null;
}

interface SaveFromReviewDialogProps {
  item: QueueItem;
  trace: TraceDetail;
  source: ReviewSource;
  open: boolean;
  onClose: () => void;
}

/**
 * Figma "Save as dataset item" (annotate): copies the trace's span into a dataset, then writes
 * why it was kept (`metadata.review`) on the copy.
 */
export function SaveFromReviewDialog({
  item,
  trace,
  source,
  open,
  onClose,
}: SaveFromReviewDialogProps) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      {open ? (
        <DialogContent
          hideClose
          className="max-h-[calc(100dvh-2rem)] max-w-[520px] overflow-y-auto rounded-card p-6 sm:p-7"
        >
          <SaveForm item={item} trace={trace} source={source} onDone={onClose} />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

interface SaveFormProps {
  item: QueueItem;
  trace: TraceDetail;
  source: ReviewSource;
  onDone: () => void;
}

function SaveForm({ item, trace, source, onDone }: SaveFormProps) {
  const [datasetId, setDatasetId] = useState("");
  // An item copied into this dataset whose `metadata.review` write failed: a retry only marks it.
  const [copied, setCopied] = useState<{ datasetId: string; itemId: string } | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);
  const save = useSaveFromReview();
  const dataset = useDatasetOption(datasetId);
  const choice = saveSpanOf(item, trace);
  const reason = item.reason ?? "manual";

  function submit(span: SaveSpan["span"]) {
    setRejection(null);
    save.mutate(
      {
        datasetId,
        traceId: trace.trace_id,
        spanId: span.span_id,
        copiedItemId: copied?.datasetId === datasetId ? copied.itemId : null,
        metadata: reviewMetadata(trace, span, {
          queue_id: source.queueId,
          item_id: item.id,
          reason,
          failure_category: source.failureCategory,
        }),
        onCopied: (itemId) => setCopied({ datasetId, itemId }),
      },
      {
        onSuccess: (rejected) => {
          if (rejected !== null) {
            setRejection(rejectionLabel(rejected));
            return;
          }
          toast.success(`Saved to “${dataset?.name ?? "the dataset"}”. ${RECORDED_NOTE}`);
          onDone();
        },
      },
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <DialogHeading
        title="Save as dataset item"
        description="Copies this trace's stored input and output into a dataset, with why it was kept."
        showClose={save.isPending ? "disabled" : true}
      />
      <DatasetSelect value={datasetId} onChange={setDatasetId} />
      {choice === null ? (
        <Notice tone="danger" role="alert">
          Nothing to save: this trace has no LLM span.
        </Notice>
      ) : (
        <div className="flex flex-col gap-2 rounded-tile bg-surface-muted px-4 py-3">
          <p className="text-overline text-muted-foreground">Saved with the item</p>
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-1.5 text-label">
            <Row label="Span">
              <code className="font-mono">{choice.span.name}</code> ({choice.which})
            </Row>
            <Row label="Reason">{reasonLabel(reason).toLowerCase()}</Row>
            <Row label="Failure category">
              {source.failureCategory === null ? (
                <UnknownValue reason="No failure category is picked for this item yet" />
              ) : (
                `${source.failureCategory} (your current label)`
              )}
            </Row>
            <Row label="Queue item">{source.queueLabel}</Row>
          </dl>
        </div>
      )}
      {rejection !== null || save.isError ? (
        <Notice tone="danger" role="alert">
          {rejection !== null
            ? `Not saved: ${rejection.toLowerCase()}.`
            : copied !== null
              ? `The item was copied, but why it was kept was not saved. ${datasetErrorMessage(save.error)}`
              : datasetErrorMessage(save.error)}
        </Notice>
      ) : null}
      <p className="flex items-start gap-1.5 text-xs font-medium text-muted-foreground">
        <Info aria-hidden className="mt-px size-3.5 shrink-0" />
        {RECORDED_NOTE}
      </p>
      <div className="flex justify-end gap-2.5">
        <DialogClose asChild>
          <Button variant="ghost" disabled={save.isPending}>
            Cancel
          </Button>
        </DialogClose>
        <Button
          variant="primary"
          disabled={!datasetId || choice === null}
          loading={save.isPending}
          onClick={() => {
            if (choice !== null) submit(choice.span);
          }}
        >
          Save item
        </Button>
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 [overflow-wrap:anywhere] text-foreground">{children}</dd>
    </>
  );
}
