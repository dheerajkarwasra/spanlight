import { useState } from "react";

import { Dialog, DialogContent } from "@/components/ui/dialog";
import type { CreatedApiKey } from "@/lib/api";

import { CreateApiKeyForm } from "./create-api-key-form";
import { SecretRevealView } from "./tokens/secret-reveal-view";

interface CreateApiKeyDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Two steps in one dialog: name the key and choose what it may do, then show its secret once.
 * The secret lives only in the dialog body's state, which unmounts when the dialog closes.
 */
export function CreateApiKeyDialog({ open, onOpenChange }: CreateApiKeyDialogProps) {
  const [pending, setPending] = useState(false);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Escape, Cancel, the close mark and an outside click all end up here: while the key is
        // being made, none of them may close the dialog and lose it.
        if (next || !pending) {
          onOpenChange(next);
        }
      }}
    >
      {/* Mounted only while open, so closing the dialog discards the secret. */}
      {open ? <CreateApiKeyDialogBody onPendingChange={setPending} /> : null}
    </Dialog>
  );
}

function CreateApiKeyDialogBody({
  onPendingChange,
}: {
  onPendingChange: (pending: boolean) => void;
}) {
  const [createdKey, setCreatedKey] = useState<CreatedApiKey | null>(null);

  return (
    <DialogContent
      hideClose
      returnFocus
      className="max-h-[calc(100dvh-2rem)] max-w-[560px] gap-0 overflow-y-auto rounded-card p-6 sm:p-7"
      onInteractOutside={(event) => {
        // A stray click outside would lose the secret for good.
        if (createdKey) {
          event.preventDefault();
        }
      }}
    >
      {createdKey ? (
        <SecretRevealView
          kind="key"
          name={createdKey.name}
          secret={createdKey.secret}
          scopes={createdKey.scopes}
          expiresAt={createdKey.expires_at}
        />
      ) : (
        <CreateApiKeyForm onCreated={setCreatedKey} onPendingChange={onPendingChange} />
      )}
    </DialogContent>
  );
}
