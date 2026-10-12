import { useNavigate } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import { useProjectParams } from "@/features/shell";
import { isApiError, type ScoreConfig } from "@/lib/api";

import { queueErrorMessage } from "./annotation-errors";
import { useCreateQueue } from "./annotation-queries";
import { ScoreConfigPicker } from "./score-config-picker";

const INSTRUCTIONS_LIMIT = 4000;

interface CreateQueueDialogProps {
  trigger: ReactNode;
  configs: readonly ScoreConfig[] | undefined;
}

/** Figma "Review — Create queue dialog": a name, instructions and the scores to collect. */
export function CreateQueueDialog({ trigger, configs }: CreateQueueDialogProps) {
  const [open, setOpen] = useState(false);
  const create = useCreateQueue();
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!create.isPending) {
          setOpen(next);
        }
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {open ? <CreateQueueForm configs={configs} create={create} /> : null}
    </Dialog>
  );
}

interface CreateQueueFormProps {
  configs: readonly ScoreConfig[] | undefined;
  create: ReturnType<typeof useCreateQueue>;
}

function CreateQueueForm({ configs, create }: CreateQueueFormProps) {
  const { orgId, projectId } = useProjectParams();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [picked, setPicked] = useState<readonly string[]>([]);
  const [failure, setFailure] = useState<unknown>(null);
  const nameTaken = isApiError(failure) && failure.code === "NAME_TAKEN";
  const ready = name.trim() !== "" && picked.length > 0;

  async function submit() {
    if (!ready) {
      return;
    }
    setFailure(null);
    try {
      const queue = await create.mutateAsync({
        name: name.trim(),
        instructions: instructions.trim() === "" ? null : instructions.trim(),
        score_config_ids: [...picked],
      });
      void navigate({
        to: "/$orgId/$projectId/queues/$queueId",
        params: { orgId, projectId, queueId: queue.id },
      });
    } catch (error) {
      setFailure(error);
    }
  }

  return (
    <DialogContent hideClose className="max-w-[560px] gap-5 rounded-card p-6 sm:p-7">
      <DialogHeading
        title="New annotation queue"
        description="Reviewers score each item against the score configs you choose."
        showClose={create.isPending ? "disabled" : true}
      />
      <form
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <FormField
          label="Name"
          hint="Shown to reviewers. Must be unique in this project."
          error={nameTaken ? queueErrorMessage(failure) : undefined}
        >
          <Input
            value={name}
            maxLength={100}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => setName(event.target.value)}
          />
        </FormField>
        <FormField
          label="Instructions"
          hint="Reviewers see these above the score inputs."
          labelAction={
            <span className="text-2xs font-medium text-muted-foreground tabular">
              {instructions.length.toLocaleString()} of {INSTRUCTIONS_LIMIT.toLocaleString()}
            </span>
          }
        >
          <Textarea
            rows={3}
            value={instructions}
            maxLength={INSTRUCTIONS_LIMIT}
            onChange={(event) => setInstructions(event.target.value)}
          />
        </FormField>
        <ScoreConfigPicker configs={configs} picked={picked} onChange={setPicked} />
        {failure === null || nameTaken ? null : (
          <Callout tone="danger" role="alert">
            {queueErrorMessage(failure)}
          </Callout>
        )}
        <div className="flex justify-end gap-2.5">
          <DialogClose asChild>
            <Button disabled={create.isPending}>Cancel</Button>
          </DialogClose>
          <Button type="submit" variant="primary" disabled={!ready} loading={create.isPending}>
            Create queue
          </Button>
        </div>
      </form>
    </DialogContent>
  );
}
