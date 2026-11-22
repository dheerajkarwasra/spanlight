import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useState, type ReactElement } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogTrigger,
} from "@/components/ui/dialog";
import { errorMessage, type Credential } from "@/lib/api";

import {
  API_KEY_HINT,
  API_KEY_MAX_LENGTH,
  PROVIDER_LABELS,
  rotateSchema,
  type RotateValues,
} from "./credential-form";
import { ApiKeyInput } from "./api-key-input";
import { NOT_CONFIGURED_COPY, isNotConfigured } from "./credential-model";
import { useRotateCredential } from "./credentials-queries";

interface RotateDialogProps {
  credential: Credential;
  /** The "Rotate" button. */
  trigger: ReactElement;
  onNotConfigured: () => void;
}

/** Figma "Credentials — Rotate dialog": one new API key field. Mounted only while open. */
export function RotateDialog({ credential, trigger, onNotConfigured }: RotateDialogProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next || !pending) {
          setOpen(next);
        }
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {open ? (
        <DialogContent
          hideClose
          className="max-h-[calc(100dvh-2rem)] max-w-[480px] gap-0 overflow-y-auto rounded-card p-6 sm:p-7"
        >
          <RotateForm
            credential={credential}
            onPendingChange={setPending}
            onDone={() => {
              setOpen(false);
            }}
            onNotConfigured={onNotConfigured}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

interface RotateFormProps {
  credential: Credential;
  onPendingChange: (pending: boolean) => void;
  onDone: () => void;
  onNotConfigured: () => void;
}

function RotateForm({ credential, onPendingChange, onDone, onNotConfigured }: RotateFormProps) {
  const rotateCredential = useRotateCredential();
  const pending = rotateCredential.pending;
  const [revealed, setRevealed] = useState(false);

  useEffect(() => {
    onPendingChange(pending);
    return () => {
      onPendingChange(false);
    };
  }, [pending, onPendingChange]);

  const form = useForm<RotateValues>({
    resolver: zodResolver(rotateSchema),
    defaultValues: { apiKey: "" },
  });
  const notConfigured = form.formState.errors.root?.type === "not-configured";

  const onSubmit = form.handleSubmit(async (values) => {
    const result = await rotateCredential.run(credential.id, values.apiKey);
    form.reset();
    setRevealed(false);
    if (result.ok) {
      toast.success(`Rotated the key of "${credential.name}".`);
      onDone();
    } else if (isNotConfigured(result.error)) {
      form.setError("root", { type: "not-configured" });
      onNotConfigured();
    } else {
      toast.error(errorMessage(result.error));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-[22px]">
      <DialogHeading
        title={`Rotate ${credential.name}`}
        description={`Paste the new key from ${PROVIDER_LABELS[credential.provider]}. Calls use it right away and the old key is discarded.`}
        showClose={pending ? "disabled" : true}
      />
      {notConfigured ? (
        <Callout tone="warning" role="alert">
          {NOT_CONFIGURED_COPY}
        </Callout>
      ) : null}
      <FormField
        label="New API key"
        hint={
          credential.provider === "azure_openai"
            ? "Use a key from the resource listed on this credential. Stored encrypted."
            : API_KEY_HINT
        }
        error={form.formState.errors.apiKey?.message}
      >
        <ApiKeyInput
          autoFocus
          revealed={revealed}
          onRevealedChange={setRevealed}
          maxLength={API_KEY_MAX_LENGTH * 2}
          {...form.register("apiKey")}
        />
      </FormField>
      <DialogFooter className="flex-row justify-end gap-2.5 max-sm:[&>*]:flex-1">
        <DialogClose asChild>
          <Button disabled={pending}>Cancel</Button>
        </DialogClose>
        <Button type="submit" variant="primary" loading={pending}>
          Rotate key
        </Button>
      </DialogFooter>
    </form>
  );
}
