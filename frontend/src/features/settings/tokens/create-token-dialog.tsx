import { useState } from "react";

import { Dialog, DialogContent } from "@/components/ui/dialog";
import type { CreatedPersonalAccessToken } from "@/lib/api";

import { CreateTokenForm } from "./create-token-form";
import { SecretRevealView } from "./secret-reveal-view";

interface CreateTokenDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Two steps in one dialog: name the token, then show it once. The token lives only in the body's
 * state, and the body is mounted only while the dialog is open, so closing it drops the secret.
 * Render it above anything a list refresh can remove: losing the mount loses the secret for good.
 */
export function CreateTokenDialog({ open, onOpenChange }: CreateTokenDialogProps) {
  const [pending, setPending] = useState(false);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Escape, Cancel, the close mark and an outside click all end up here: while the token is
        // being made, none of them may close the dialog and lose it.
        if (next || !pending) {
          onOpenChange(next);
        }
      }}
    >
      {open ? <CreateTokenDialogBody onPendingChange={setPending} /> : null}
    </Dialog>
  );
}

/** A bottom sheet below 640 px (Figma "Create token dialog — 375"), a centred dialog above. */
const SHEET_CLASSES = [
  "max-w-[560px] gap-0 rounded-card p-6 sm:p-7",
  "max-h-[calc(100dvh-2rem)] overflow-y-auto",
  "max-sm:top-auto max-sm:bottom-0 max-sm:left-0 max-sm:w-full max-sm:max-w-none max-sm:translate-x-0 max-sm:translate-y-0",
  "max-sm:rounded-t-3xl max-sm:rounded-b-none max-sm:p-[21px]",
].join(" ");

function CreateTokenDialogBody({
  onPendingChange,
}: {
  onPendingChange: (pending: boolean) => void;
}) {
  const [created, setCreated] = useState<CreatedPersonalAccessToken | null>(null);

  return (
    <DialogContent
      hideClose
      returnFocus
      className={SHEET_CLASSES}
      onInteractOutside={(event) => {
        // A stray click outside would lose the token for good.
        if (created) {
          event.preventDefault();
        }
      }}
    >
      <div
        aria-hidden
        className="mx-auto mb-[18px] h-1 w-9 rounded-full bg-border-strong sm:hidden"
      />
      {created ? (
        <SecretRevealView
          kind="token"
          name={created.name}
          secret={created.token}
          scopes={[created.scope]}
          expiresAt={created.expires_at}
        />
      ) : (
        <CreateTokenForm onCreated={setCreated} onPendingChange={onPendingChange} />
      )}
    </DialogContent>
  );
}
