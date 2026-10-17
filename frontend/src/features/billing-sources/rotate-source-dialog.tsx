import { useEffect, useState, type ReactElement } from "react";
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
import { ApiKeyInput } from "@/features/gateway";
import { errorMessage, type BillingSource } from "@/lib/api";

import { useRotateSource } from "./billing-sources-queries";
import {
  ADMIN_KEY_MAX_LENGTH,
  adminKeyError,
  adminKeyHint,
  isApiKind,
  isNotConfigured,
  sourceErrorsOf,
} from "./source-form";
import { PROVIDER_LABELS } from "./source-model";

interface RotateSourceDialogProps {
  /** An API-kind source; a CSV source holds no key. */
  source: BillingSource;
  /** The element that opens the dialog: Rotate key in the edit dialog or the failing callout. */
  trigger: ReactElement;
}

/** Figma "Billing sources / rotate": one new admin key field. Mounted only while open. */
export function RotateSourceDialog({ source, trigger }: RotateSourceDialogProps) {
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
          onOpenAutoFocus={(event) => {
            // The key field, not the close mark; also when opened from inside the edit dialog,
            // whose focus trap would otherwise hand focus back to its own last field.
            const field =
              event.currentTarget instanceof HTMLElement
                ? event.currentTarget.querySelector<HTMLElement>('input[type="password"]')
                : null;
            if (field) {
              event.preventDefault();
              field.focus();
            }
          }}
        >
          <RotateForm
            source={source}
            onPendingChange={setPending}
            onDone={() => {
              setOpen(false);
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

interface RotateFormProps {
  source: BillingSource;
  onPendingChange: (pending: boolean) => void;
  onDone: () => void;
}

function RotateForm({ source, onPendingChange, onDone }: RotateFormProps) {
  const rotate = useRotateSource();
  const [adminKey, setAdminKey] = useState("");
  const [revealed, setRevealed] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [notConfigured, setNotConfigured] = useState<string | null>(null);
  const kind = isApiKind(source.kind) ? source.kind : null;

  useEffect(() => {
    onPendingChange(rotate.pending);
    return () => {
      onPendingChange(false);
    };
  }, [rotate.pending, onPendingChange]);

  async function submit() {
    const invalid = kind ? adminKeyError(kind, adminKey) : "A CSV source holds no key.";
    setError(invalid);
    if (invalid) {
      return;
    }
    const result = await rotate.run(source.id, adminKey.trim());
    // The typed key leaves the page once it has been sent, whatever the answer.
    setAdminKey("");
    setRevealed(false);
    if (result.ok) {
      const rotated = result.value;
      if (rotated.status === "failing") {
        toast.warning(
          `Stored the new key for "${source.name}", but its check failed: ${rotated.last_error ?? "unknown error"}.`,
        );
      } else {
        toast.success(`Rotated the key for "${source.name}".`);
      }
      onDone();
    } else if (isNotConfigured(result.error)) {
      setNotConfigured(errorMessage(result.error));
    } else {
      const fieldErrors = sourceErrorsOf(result.error);
      if (fieldErrors?.adminKey) {
        setError(fieldErrors.adminKey);
      } else {
        toast.error(errorMessage(result.error));
      }
    }
  }

  return (
    <form
      noValidate
      className="grid gap-[22px]"
      onSubmit={(event) => {
        // The edit dialog's form is an ancestor in the React tree: keep this submit to itself.
        event.preventDefault();
        event.stopPropagation();
        void submit();
      }}
    >
      <DialogHeading
        title={`Rotate the key for ${source.name}`}
        description={`Paste the new Admin API key from ${PROVIDER_LABELS[source.provider]}. The next fetch uses it and the old key is discarded.`}
        showClose={rotate.pending ? "disabled" : true}
      />
      {notConfigured ? (
        <Callout tone="warning" role="alert">
          {notConfigured}
        </Callout>
      ) : null}
      <FormField
        label="New Admin API key"
        hint={kind ? adminKeyHint(kind) : undefined}
        error={error}
      >
        <ApiKeyInput
          revealed={revealed}
          onRevealedChange={setRevealed}
          maxLength={ADMIN_KEY_MAX_LENGTH * 2}
          value={adminKey}
          onChange={(event) => {
            setAdminKey(event.target.value);
            setError(undefined);
          }}
        />
      </FormField>
      <DialogFooter className="flex-row justify-end gap-2.5 max-sm:[&>*]:flex-1">
        <DialogClose asChild>
          <Button disabled={rotate.pending}>Cancel</Button>
        </DialogClose>
        <Button type="submit" variant="primary" loading={rotate.pending}>
          Rotate key
        </Button>
      </DialogFooter>
    </form>
  );
}
