import { useNavigate } from "@tanstack/react-router";
import { useState, type ReactElement, type SubmitEvent } from "react";
import { toast } from "sonner";

import { DialogHeading } from "@/components/dialog-heading";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import { useProjectParams } from "@/features/shell";

import { datasetErrorMessage, isProblem, nameTakenMessage } from "./dataset-errors";
import { useCreateDataset } from "./dataset-queries";

const NAME_MAX = 100;

/** Figma "Datasets — Create dialog" (and its name-taken state). Opens the new dataset on save. */
export function CreateDatasetDialog({ trigger }: { trigger: ReactElement }) {
  const [open, setOpen] = useState(false);
  const create = useCreateDataset();

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next || !create.isPending) {
          setOpen(next);
          if (!next) create.reset();
        }
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {open ? (
        <DialogContent hideClose className="max-w-[520px] rounded-card p-6 sm:p-7">
          <CreateDatasetForm
            create={create}
            onCreated={() => {
              setOpen(false);
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

interface CreateDatasetFormProps {
  create: ReturnType<typeof useCreateDataset>;
  onCreated: () => void;
}

function CreateDatasetForm({ create, onCreated }: CreateDatasetFormProps) {
  const navigate = useNavigate();
  const { orgId, projectId } = useProjectParams();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submittedName, setSubmittedName] = useState("");
  const trimmed = name.trim();
  const nameTaken = isProblem(create.error, "NAME_TAKEN") && trimmed === submittedName;
  const otherError = create.error !== null && !isProblem(create.error, "NAME_TAKEN");

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!trimmed) return;
    setSubmittedName(trimmed);
    create.mutate(
      { name: trimmed, description: description.trim() || null },
      {
        onSuccess: (dataset) => {
          toast.success(`Created dataset “${dataset.name}”.`);
          onCreated();
          void navigate({
            to: "/$orgId/$projectId/datasets/$datasetId",
            params: { orgId, projectId, datasetId: dataset.id },
          });
        },
      },
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5">
      <DialogHeading
        title="New dataset"
        description="A dataset is a named set of inputs and expected outputs. The CLI and the GitHub Action use its name."
        showClose={create.isPending ? "disabled" : true}
      />
      <FormField
        label="Name"
        error={nameTaken ? nameTakenMessage(trimmed) : undefined}
        hint="Unique in this project. Up to 100 characters."
      >
        <Input
          value={name}
          maxLength={NAME_MAX}
          autoComplete="off"
          spellCheck={false}
          placeholder="support-questions"
          className="font-mono text-label"
          onChange={(event) => {
            setName(event.target.value);
          }}
        />
      </FormField>
      <FormField label="Description (optional)">
        <Textarea
          value={description}
          maxLength={1000}
          rows={3}
          onChange={(event) => {
            setDescription(event.target.value);
          }}
        />
      </FormField>
      {otherError ? (
        <Notice tone="danger" role="alert">
          {datasetErrorMessage(create.error)}
        </Notice>
      ) : null}
      <div className="flex justify-end gap-2.5">
        <DialogClose asChild>
          <Button disabled={create.isPending}>Cancel</Button>
        </DialogClose>
        <Button
          type="submit"
          variant="primary"
          loading={create.isPending}
          disabled={!trimmed || nameTaken}
        >
          Create dataset
        </Button>
      </div>
    </form>
  );
}
