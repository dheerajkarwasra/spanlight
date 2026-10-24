import { Pencil } from "lucide-react";
import { useState, type SubmitEvent } from "react";
import { toast } from "sonner";

import { DialogHeading } from "@/components/dialog-heading";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import type { Dataset } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { datasetErrorMessage } from "./dataset-errors";
import { useUpdateDataset } from "./dataset-queries";

/** The API's limit on a dataset description. */
const DESCRIPTION_MAX = 1000;

/**
 * Figma "E7 — Edit description". Only the description is editable: SDKs and the CLI fetch a
 * dataset by name, so renaming would break them. An empty field clears the description.
 */
export function EditDescriptionDialog({ dataset }: { dataset: Dataset }) {
  const [open, setOpen] = useState(false);
  const update = useUpdateDataset(dataset.id);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next || !update.isPending) {
          setOpen(next);
          if (!next) update.reset();
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant="ghost">
          <Pencil aria-hidden />
          Edit description
        </Button>
      </DialogTrigger>
      {open ? (
        <DialogContent hideClose className="max-w-[520px] rounded-card p-6 sm:p-7">
          <DescriptionForm
            initial={dataset.description ?? ""}
            update={update}
            onSaved={() => {
              setOpen(false);
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

interface DescriptionFormProps {
  initial: string;
  update: ReturnType<typeof useUpdateDataset>;
  onSaved: () => void;
}

function DescriptionForm({ initial, update, onSaved }: DescriptionFormProps) {
  const [description, setDescription] = useState(initial);

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    update.mutate(
      { description: description.trim() || null },
      {
        onSuccess: () => {
          toast.success("Description saved.");
          onSaved();
        },
      },
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5">
      <DialogHeading
        title="Edit description"
        description="Shown on the dataset list and here. The name stays the same."
        showClose={update.isPending ? "disabled" : true}
      />
      <FormField
        label="Description"
        hint={
          <span className="block text-right tabular">
            {formatInteger(description.length)} / {formatInteger(DESCRIPTION_MAX)}
          </span>
        }
      >
        <Textarea
          value={description}
          maxLength={DESCRIPTION_MAX}
          rows={3}
          onChange={(event) => {
            setDescription(event.target.value);
          }}
        />
      </FormField>
      {update.error ? (
        <Notice tone="danger" role="alert">
          {datasetErrorMessage(update.error)}
        </Notice>
      ) : null}
      <div className="flex justify-end gap-2.5">
        <DialogClose asChild>
          <Button disabled={update.isPending}>Cancel</Button>
        </DialogClose>
        <Button type="submit" variant="primary" loading={update.isPending}>
          Save
        </Button>
      </div>
    </form>
  );
}
