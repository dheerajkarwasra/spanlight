import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import { pluralize } from "@/lib/format";

import { queueErrorMessage, unknownTraceIds } from "./annotation-errors";
import { useAddItems } from "./annotation-queries";
import { MAX_TRACE_IDS, parseTraceIds } from "./queue-format";

interface AddItemsDialogProps {
  queueId: string;
  queueName: string;
  trigger: ReactNode;
}

/**
 * Figma "Review — Add items dialog": paste trace ids, one per line. Ids that cannot be trace ids
 * are flagged before sending; traces the project does not have come back from the server and are
 * listed so the rest can be sent again.
 */
export function AddItemsDialog({ queueId, queueName, trigger }: AddItemsDialogProps) {
  const [open, setOpen] = useState(false);
  const add = useAddItems(queueId);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!add.isPending) {
          setOpen(next);
        }
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {open ? <AddItemsForm queueName={queueName} add={add} onDone={() => setOpen(false)} /> : null}
    </Dialog>
  );
}

interface AddItemsFormProps {
  queueName: string;
  add: ReturnType<typeof useAddItems>;
  onDone: () => void;
}

function AddItemsForm({ queueName, add, onDone }: AddItemsFormProps) {
  const [text, setText] = useState("");
  const [failure, setFailure] = useState<unknown>(null);
  const { ids, malformed } = parseTraceIds(text);
  const unknown = unknownTraceIds(failure);
  const tooMany = ids.length > MAX_TRACE_IDS;
  const lines = text.split("\n").filter((line) => line.trim() !== "").length;

  async function submit() {
    if (ids.length === 0 || malformed.length > 0 || tooMany) {
      return;
    }
    setFailure(null);
    try {
      const result = await add.mutateAsync(ids);
      const skipped =
        result.skipped_duplicates > 0
          ? ` ${pluralize(result.skipped_duplicates, "trace")} already in the queue.`
          : "";
      toast.success(`Added ${pluralize(result.added, "item")}.${skipped}`);
      onDone();
    } catch (error) {
      setFailure(error);
    }
  }

  return (
    <DialogContent hideClose className="max-w-[560px] gap-5 rounded-card p-6 sm:p-7">
      <DialogHeading
        title={`Add items to ${queueName}`}
        description="Paste trace ids, one per line. You can also add traces from the Traces page with “Add to queue”."
        showClose={add.isPending ? "disabled" : true}
      />
      <form
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <FormField
          label="Trace ids"
          hint={`Up to ${String(MAX_TRACE_IDS)} at a time. A trace already in the queue is skipped.`}
          labelAction={
            <span className="text-2xs font-medium text-muted-foreground">
              {pluralize(lines, "line")}
            </span>
          }
        >
          <Textarea
            rows={5}
            value={text}
            spellCheck={false}
            autoComplete="off"
            className="font-mono text-label"
            onChange={(event) => {
              setText(event.target.value);
              setFailure(null);
            }}
          />
        </FormField>
        {malformed.length > 0 ? (
          <Callout tone="warning" role="status" title="Not a trace id">
            {malformed.slice(0, 5).join(", ")}
            {malformed.length > 5 ? ` and ${String(malformed.length - 5)} more` : ""}. A trace id is
            32 hexadecimal characters. Fix or remove these lines.
          </Callout>
        ) : null}
        {tooMany ? (
          <Callout tone="warning" role="status">
            {`That is ${pluralize(ids.length, "trace")}. Add at most ${String(MAX_TRACE_IDS)} at a time.`}
          </Callout>
        ) : null}
        {failure === null ? null : (
          <Callout
            tone="danger"
            role="alert"
            title={unknown.length > 0 ? "Trace not found" : undefined}
          >
            {unknown.length > 0
              ? `${unknown.join(", ")}: not in this project. Remove ${unknown.length === 1 ? "it" : "them"} and add the rest.`
              : queueErrorMessage(failure)}
          </Callout>
        )}
        <div className="flex justify-end gap-2.5">
          <DialogClose asChild>
            <Button disabled={add.isPending}>Cancel</Button>
          </DialogClose>
          <Button
            type="submit"
            variant="primary"
            disabled={ids.length === 0 || malformed.length > 0 || tooMany}
            loading={add.isPending}
          >
            {ids.length === 0 ? "Add items" : `Add ${pluralize(ids.length, "item")}`}
          </Button>
        </div>
      </form>
    </DialogContent>
  );
}
