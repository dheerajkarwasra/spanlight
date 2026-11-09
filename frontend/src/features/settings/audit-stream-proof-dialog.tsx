import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import type { AuditStreamProof } from "@/lib/api/orgs";

import { MemberReauthFields } from "./member-reauth";
import { useMemberReauth } from "./use-member-reauth";

const COPY = {
  pause: {
    title: "Pause streaming?",
    description: "Events wait until you turn it on. Confirm it's you first.",
    button: "Pause",
  },
  resume: {
    title: "Resume streaming?",
    description: "Events waiting so far are sent. Confirm it's you first.",
    button: "Resume",
  },
  rotate: {
    title: "Rotate the signing secret?",
    description: "The old one stops now; update your receiver. Confirm it's you first.",
    button: "Rotate",
  },
  remove: {
    title: "Stop streaming?",
    description:
      "The endpoint and its signing secret are deleted. Events stay in the audit log. Confirm it's you first.",
    button: "Remove",
  },
};

export type AuditStreamAction = keyof typeof COPY;

export interface AuditStreamChange {
  action: AuditStreamAction;
  opener: HTMLElement;
}

export function AuditStreamProofDialog({
  change,
  pending,
  onSubmit,
  onClose,
}: {
  change: AuditStreamChange;
  pending: boolean;
  onSubmit: (proof: AuditStreamProof) => Promise<void>;
  onClose: () => void;
}) {
  const confirmation = useMemberReauth();
  const { form, reauth, formError } = confirmation;
  const copy = COPY[change.action];
  const submit = form.handleSubmit(async (values) => {
    if (pending) return;
    const proof = confirmation.proof(values);
    if (!proof) return;
    try {
      await onSubmit(proof);
      onClose();
    } catch (error) {
      confirmation.onError(error);
    }
  });

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !pending) onClose();
      }}
    >
      <DialogContent
        hideClose
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          // Removing the stream takes the button that opened this away with it.
          (change.opener.isConnected ? change.opener : document.getElementById("main"))?.focus();
        }}
        className="max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-card p-5 sm:p-6"
      >
        <DialogHeading
          title={copy.title}
          description={copy.description}
          showClose={pending ? "disabled" : true}
        />
        <form noValidate onSubmit={submit} className="flex flex-col gap-5">
          <MemberReauthFields
            confirmation={confirmation}
            pending={pending}
            action="Changing audit streaming"
          />
          {formError ? (
            <Callout tone="danger" role="alert">
              {formError}
            </Callout>
          ) : null}
          <DialogFooter>
            <Button onClick={onClose} disabled={pending}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant={change.action === "remove" ? "danger" : "primary"}
              disabled={reauth}
              loading={pending}
            >
              {copy.button}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
