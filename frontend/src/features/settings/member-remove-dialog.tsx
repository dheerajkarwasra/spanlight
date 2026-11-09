import { useRef } from "react";
import { toast } from "sonner";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { DisabledReason } from "@/components/disabled-reason";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { useCurrentOrg } from "@/features/shell";
import type { Member } from "@/lib/api";

import { useRemoveMember } from "./member-queries";
import { MemberReauthFields } from "./member-reauth";
import { displayName, memberRemovalKeyLines } from "./member-utils";
import { useMemberReauth } from "./use-member-reauth";

export interface MemberRemoval {
  member: Member;
  button: HTMLElement;
}

export function MemberRemoveDialog({
  removal,
  onClose,
}: {
  removal: MemberRemoval;
  onClose: () => void;
}) {
  const remove = useRemoveMember();
  const confirmation = useMemberReauth();
  const { form, me, reauth, formError } = confirmation;
  const org = useCurrentOrg();
  const orgName = org?.name ?? "this organization";
  const name = displayName(removal.member);
  const keyLines = memberRemovalKeyLines(removal.member.read_keys);
  const card = useRef<HTMLElement | null>(null);
  const pending = remove.isPending;
  const onSubmit = form.handleSubmit(async (values) => {
    if (pending) return;
    const proof = confirmation.proof(values);
    if (!proof) return;
    try {
      await remove.mutateAsync({ userId: removal.member.user.id, ...proof });
      toast.success(`Removed ${name} from ${orgName}.`);
      onClose();
    } catch (error) {
      confirmation.onError(error);
    }
  });
  const submit = (
    <Button type="submit" variant="danger" disabled={reauth} loading={pending}>
      Remove member
    </Button>
  );
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !pending) onClose();
      }}
    >
      <DialogContent
        hideClose
        onOpenAutoFocus={(event) => {
          card.current = removal.button.closest<HTMLElement>('[role="region"]');
          if (me.has_password || me.totp_enabled) {
            event.preventDefault();
            form.setFocus(me.has_password ? "password" : "code");
          }
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          if (removal.button.isConnected) {
            removal.button.focus();
          } else if (card.current?.isConnected) {
            card.current.tabIndex = -1;
            card.current.focus();
          }
        }}
        className="max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-card p-5 sm:p-6"
      >
        <DialogHeading
          title={`Remove ${name} from ${orgName}?`}
          description={`They lose access to every project in ${orgName} now.`}
          showClose={pending ? "disabled" : true}
        />
        <div className="flex flex-col gap-2 rounded-tile bg-surface-muted px-4 py-3.5">
          <p className="text-overline text-subtle-foreground uppercase">Their keys</p>
          {keyLines.map((line) => (
            <p key={line} className="text-sm text-muted-foreground">
              {line}
            </p>
          ))}
        </div>
        <form noValidate onSubmit={onSubmit} className="flex flex-col gap-5">
          <MemberReauthFields
            confirmation={confirmation}
            pending={pending}
            action="Removing a member"
          />
          <p role="status" className="sr-only">
            {form.formState.errors.password?.message ?? form.formState.errors.code?.message ?? ""}
          </p>
          <p className="text-xs font-medium text-muted-foreground">
            If email is set up, {name} and the other owners are told.
          </p>
          {formError ? (
            <Callout tone="danger" role="alert">
              {formError}
            </Callout>
          ) : null}
          <DialogFooter>
            <Button onClick={onClose} disabled={pending}>
              Cancel
            </Button>
            {reauth ? (
              <DisabledReason reason="Sign in again first.">{submit}</DisabledReason>
            ) : (
              submit
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
