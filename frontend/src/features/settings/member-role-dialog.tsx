import { toast } from "sonner";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { DisabledReason } from "@/components/disabled-reason";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import type { Member, Role } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/permissions";

import { useUpdateMemberRole } from "./member-queries";
import { MemberReauthFields } from "./member-reauth";
import { useMemberReauth } from "./use-member-reauth";
import { displayName, memberRoleDescription, roleWithArticle } from "./member-utils";

export interface MemberRoleChange {
  member: Member;
  role: Role;
  /** The role select, for focus on close: what held focus when this opened is its removed menu. */
  select: HTMLElement | null;
}

export function MemberRoleDialog({
  change,
  onClose,
}: {
  change: MemberRoleChange;
  onClose: () => void;
}) {
  const update = useUpdateMemberRole();
  const confirmation = useMemberReauth();
  const { form, reauth, formError } = confirmation;
  const name = displayName(change.member);
  const pending = update.isPending;
  const onSubmit = form.handleSubmit(async (values) => {
    if (pending) return;
    const proof = confirmation.proof(values);
    if (!proof) return;
    try {
      await update.mutateAsync({ userId: change.member.user.id, role: change.role, ...proof });
      toast.success(`${name} is now ${ROLE_LABELS[change.role].toLowerCase()}.`);
      onClose();
    } catch (error) {
      confirmation.onError(error);
    }
  });
  const submit = (
    <Button type="submit" variant="primary" disabled={reauth} loading={pending}>
      Change role
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
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          change.select?.focus();
        }}
        className="max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-card p-5 sm:p-6"
      >
        <DialogHeading
          title={`Make ${name} ${roleWithArticle(change.role)}?`}
          description={memberRoleDescription(change.member.role, change.role)}
          showClose={pending ? "disabled" : true}
        />
        <form noValidate onSubmit={onSubmit} className="flex flex-col gap-5">
          <MemberReauthFields
            confirmation={confirmation}
            pending={pending}
            action="Changing a role"
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
